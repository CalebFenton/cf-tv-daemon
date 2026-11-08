using System;
using System.Collections.Generic;
using System.IO;
using System.Runtime.InteropServices;
using System.Threading;

/// <summary>
/// Breakpoint tracer for a running process, ARMv7 Linux, Thumb code.
///
///     dotnet BtTrace.dll PID SECONDS LIB:FILEOFF[:LABEL] [...]
///
/// LIB is a substring of the mapped file path; FILEOFF is the function's file
/// offset in hex (for a prelinked library this is st_value - first PT_LOAD
/// vaddr, with the Thumb bit cleared). The runtime address comes from the
/// executable mapping that covers FILEOFF in /proc/PID/maps, so no load-bias
/// arithmetic is needed.
///
/// On each hit: registers, r0 as a C string, the thread name, and every stack
/// word that points into executable code (a heuristic backtrace).
///
/// Mechanics and why:
/// - PTRACE_SEIZE every thread with TRACECLONE, so threads created later are
///   traced too. SEIZE (not ATTACH) does not stop the target.
/// - The breakpoint is Thumb 0xDE01: the kernel's thumb_break_hook turns
///   exactly that into SIGTRAP with pc at the instruction. 0xDE00 is SIGILL.
/// - ARM Linux has no PTRACE_SINGLESTEP, so stepping over a hit restores the
///   original instruction, plants a temporary breakpoint after it, and re-arms
///   the real one when the temporary one fires.
/// - All ptrace calls must come from the thread that seized, so everything,
///   including cleanup on SIGTERM, runs on the main thread.
/// - Cleanup interrupts every thread, restores code, and detaches. A
///   breakpoint left behind would SIGTRAP the untraced target, so the run is
///   time-bounded and SIGTERM is routed into the same cleanup.
/// </summary>
internal static class Program
{
    const int PEEKDATA = 2, POKEDATA = 5, CONT = 7, GETREGS = 12, DETACH = 17;
    const int GETEVENTMSG = 0x4201, SEIZE = 0x4206, INTERRUPT = 0x4207;
    const int O_TRACECLONE = 8, EVENT_CLONE = 3, EVENT_STOP = 128;
    const int WNOHANG = 1, WALL = 0x40000000;
    const int SIGTRAP = 5;
    const ushort BKPT_THUMB = 0xDE01;

    [DllImport("libc", SetLastError = true)] static extern int ptrace(int req, int pid, IntPtr addr, IntPtr data);
    [DllImport("libc", SetLastError = true)] static extern int waitpid(int pid, out int status, int options);
    [DllImport("libc", SetLastError = true)] static extern int ptrace(int req, int pid, IntPtr addr, uint[] regs);
    [DllImport("libc")] static extern IntPtr __errno_location();

    sealed class Bp { public uint Addr; public string Label; public ushort Orig; public int Width; public bool Armed; public bool Temp; public Bp Owner; }

    static int pid;
    static readonly HashSet<int> threads = new HashSet<int>();
    static readonly Dictionary<uint, Bp> bps = new Dictionary<uint, Bp>();
    static volatile bool stopRequested;
    static readonly ManualResetEventSlim cleaned = new ManualResetEventSlim();
    static List<(uint lo, uint hi, uint off, string path, bool exec)> maps;

    static int Main(string[] args)
    {
        if (args.Length < 3) { Console.Error.WriteLine("usage: PID SECONDS LIB:FILEOFF[:LABEL] ..."); return 2; }
        pid = int.Parse(args[0]);
        var deadline = DateTime.UtcNow.AddSeconds(double.Parse(args[1]));
        maps = LoadMaps();

        foreach (var spec in args[2..])
        {
            var p = spec.Split(':');
            uint fileoff = Convert.ToUInt32(p[1], 16) & ~1u;
            var m = maps.Find(x => x.exec && x.path.Contains(p[0]) && fileoff >= x.off && fileoff < x.off + (x.hi - x.lo));
            if (m.path == null) { Console.Error.WriteLine($"no executable mapping of {p[0]} covers 0x{fileoff:x}"); return 2; }
            uint addr = m.lo + (fileoff - m.off);
            bps[addr] = new Bp { Addr = addr, Label = p.Length > 2 ? p[2] : spec };
            Console.WriteLine($"bp {bps[addr].Label}: {Path.GetFileName(m.path)}+0x{fileoff:x} -> 0x{addr:x}");
        }

        AppDomain.CurrentDomain.ProcessExit += (s, e) => { stopRequested = true; cleaned.Wait(3000); };
        Console.CancelKeyPress += (s, e) => { e.Cancel = true; stopRequested = true; };

        try
        {
            if (!SeizeAll()) return 1;
            int stopped = InterruptAndWait(pid);
            if (stopped < 0) return 1;
            foreach (var bp in bps.Values)
            {
                ushort hw = Peek16(pid, bp.Addr);
                bp.Width = (hw & 0xF800) >= 0xE800 ? 4 : 2;
                Arm(pid, bp);
                Console.WriteLine($"armed {bp.Label} orig=0x{bp.Orig:x4} width={bp.Width}");
            }
            Cont(pid, 0);
            Console.WriteLine($"tracing {threads.Count} threads of {pid} until deadline");
            Loop(deadline);
        }
        finally
        {
            Cleanup();
            cleaned.Set();
        }
        return 0;
    }

    static void Loop(DateTime deadline)
    {
        while (!stopRequested && DateTime.UtcNow < deadline && threads.Count > 0)
        {
            int tid = waitpid(-1, out int st, WALL | WNOHANG);
            if (tid == 0) { Thread.Sleep(5); continue; }
            if (tid < 0) { Console.WriteLine($"waitpid errno={Marshal.GetLastWin32Error()}"); return; }
            Handle(tid, st);
        }
        Console.WriteLine(stopRequested ? "stop requested" : threads.Count == 0 ? "target exited" : "deadline");
    }

    static void Handle(int tid, int st)
    {
        if ((st & 0xff) != 0x7f)   // not stopped: exited or killed
        {
            threads.Remove(tid);
            return;
        }
        int sig = (st >> 8) & 0xff, ev = st >> 16;
        if (ev == EVENT_CLONE)
        {
            IntPtr buf = Marshal.AllocHGlobal(4);
            ptrace(GETEVENTMSG, tid, IntPtr.Zero, buf);
            threads.Add(Marshal.ReadInt32(buf));
            Marshal.FreeHGlobal(buf);
            Cont(tid, 0);
            return;
        }
        if (ev == EVENT_STOP) { threads.Add(tid); Cont(tid, 0); return; }
        if (sig == SIGTRAP)
        {
            var r = Regs(tid);
            if (r != null && bps.TryGetValue(r[15], out var bp) && bp.Armed)
            {
                if (bp.Temp) StepDone(tid, bp); else Hit(tid, bp, r);
                Cont(tid, 0);
                return;
            }
        }
        Cont(tid, sig);  // not ours: deliver the signal unchanged
    }

    static void Hit(int tid, Bp bp, uint[] r)
    {
        string comm = "";
        try { comm = File.ReadAllText($"/proc/{pid}/task/{tid}/comm").Trim(); } catch { }
        Console.WriteLine($"\n=== HIT {bp.Label} tid={tid} ({comm}) at {DateTime.Now:HH:mm:ss.fff}");
        for (int i = 0; i < 4; i++)
            Console.WriteLine($"  r{i}=0x{r[i]:x8} {r[i],11}  str={Str(tid, r[i])}");
        Console.WriteLine($"  sp=0x{r[13]:x8} lr=0x{r[14]:x8} {Where(r[14])}");
        Console.WriteLine("  stack code pointers:");
        int shown = 0;
        for (int i = 0; i < 256 && shown < 16; i++)
        {
            uint w = PeekWord(tid, r[13] + (uint)(i * 4), out bool ok);
            if (!ok) break;
            string where = Where(w);
            if (where.Length > 0) { Console.WriteLine($"    [sp+0x{i * 4:x3}] 0x{w:x8} {where}"); shown++; }
        }
        // step over: restore the original, catch the next instruction
        Disarm(tid, bp);
        uint next = bp.Addr + (uint)bp.Width;
        if (!bps.ContainsKey(next))
        {
            var t = new Bp { Addr = next, Label = bp.Label + "+step", Temp = true, Owner = bp, Width = 2 };
            bps[next] = t;
            Arm(tid, t);
        }
    }

    static void StepDone(int tid, Bp temp)
    {
        Disarm(tid, temp);
        bps.Remove(temp.Addr);
        Arm(tid, temp.Owner);
    }

    static string Where(uint a)
    {
        foreach (var m in maps)
            if (m.exec && a >= m.lo && a < m.hi)
                return $"{Path.GetFileName(m.path)}+0x{a - m.lo + m.off:x}";
        return "";
    }

    static string Str(int tid, uint a)
    {
        if (a < 0x10000) return "-";
        var bytes = new List<byte>();
        for (int i = 0; i < 16; i++)
        {
            uint w = PeekWord(tid, a + (uint)(i * 4), out bool ok);
            if (!ok) return "-";
            for (int b = 0; b < 4; b++)
            {
                byte c = (byte)(w >> (8 * b));
                if (c == 0) return bytes.Count > 0 ? "\"" + System.Text.Encoding.ASCII.GetString(bytes.ToArray()) + "\"" : "-";
                if (c < 0x20 || c > 0x7e) return "-";
                bytes.Add(c);
            }
        }
        return "-";
    }

    // ------------------------------------------------------------ attach/detach

    static bool SeizeAll()
    {
        for (int pass = 0; pass < 10; pass++)
        {
            bool added = false;
            foreach (var d in Directory.GetDirectories($"/proc/{pid}/task"))
            {
                int tid = int.Parse(Path.GetFileName(d));
                if (threads.Contains(tid)) continue;
                if (ptrace(SEIZE, tid, IntPtr.Zero, (IntPtr)O_TRACECLONE) != 0)
                {
                    int e = Marshal.GetLastWin32Error();
                    if (e == 3) continue;  // ESRCH: thread already gone
                    Console.Error.WriteLine($"SEIZE {tid} errno={e}");
                    return false;
                }
                threads.Add(tid);
                added = true;
            }
            if (!added) { Console.WriteLine($"seized {threads.Count} threads"); return true; }
        }
        return true;
    }

    static int InterruptAndWait(int tid)
    {
        if (ptrace(INTERRUPT, tid, IntPtr.Zero, IntPtr.Zero) != 0)
        { Console.Error.WriteLine($"INTERRUPT {tid} errno={Marshal.GetLastWin32Error()}"); return -1; }
        while (true)
        {
            int got = waitpid(tid, out int st, WALL);
            if (got < 0) { Console.Error.WriteLine($"waitpid {tid} errno={Marshal.GetLastWin32Error()}"); return -1; }
            if ((st & 0xff) != 0x7f) { threads.Remove(tid); return -1; }
            if ((st >> 16) == EVENT_STOP) return tid;
            Pending[tid] = (st >> 8) & 0xff;  // a real signal arrived first; deliver it at detach
            if (Pending[tid] == SIGTRAP) Pending[tid] = 0;  // our breakpoint; pc is at it, code is restored before detach
        }
    }

    static readonly Dictionary<int, int> Pending = new Dictionary<int, int>();

    static void Cleanup()
    {
        var stoppedThreads = new List<int>();
        foreach (int tid in new List<int>(threads))
            if (InterruptAndWait(tid) >= 0) stoppedThreads.Add(tid);
        if (stoppedThreads.Count > 0)
            foreach (var bp in bps.Values)
                if (bp.Armed) Disarm(stoppedThreads[0], bp);
        foreach (int tid in stoppedThreads)
        {
            Pending.TryGetValue(tid, out int sig);
            ptrace(DETACH, tid, IntPtr.Zero, (IntPtr)sig);
        }
        Console.WriteLine($"detached {stoppedThreads.Count} threads, code restored");
    }

    // ------------------------------------------------------------ memory

    static uint PeekWord(int tid, uint addr, out bool ok)
    {
        Marshal.WriteInt32(__errno_location(), 0);  // PEEKDATA returns data; -1 is ambiguous without errno
        int v = ptrace(PEEKDATA, tid, (IntPtr)(int)addr, IntPtr.Zero);
        ok = !(v == -1 && Marshal.ReadInt32(__errno_location()) != 0);
        return (uint)v;
    }

    static ushort Peek16(int tid, uint addr)
    {
        uint w = PeekWord(tid, addr & ~3u, out bool ok);
        if (!ok) throw new Exception($"cannot read 0x{addr:x}");
        return (ushort)((addr & 2) != 0 ? w >> 16 : w & 0xffff);
    }

    static void Poke16(int tid, uint addr, ushort val)
    {
        uint a = addr & ~3u;
        uint w = PeekWord(tid, a, out bool ok);
        if (!ok) throw new Exception($"cannot read 0x{a:x}");
        w = (addr & 2) != 0 ? (w & 0x0000ffffu) | ((uint)val << 16) : (w & 0xffff0000u) | val;
        if (ptrace(POKEDATA, tid, (IntPtr)(int)a, (IntPtr)(int)w) != 0)
            throw new Exception($"POKE 0x{a:x} errno={Marshal.GetLastWin32Error()}");
    }

    static void Arm(int tid, Bp bp) { bp.Orig = Peek16(tid, bp.Addr); Poke16(tid, bp.Addr, BKPT_THUMB); bp.Armed = true; }
    static void Disarm(int tid, Bp bp) { Poke16(tid, bp.Addr, bp.Orig); bp.Armed = false; }

    static uint[] Regs(int tid)
    {
        var r = new uint[18];
        return ptrace(GETREGS, tid, IntPtr.Zero, r) == 0 ? r : null;
    }

    static void Cont(int tid, int sig) => ptrace(CONT, tid, IntPtr.Zero, (IntPtr)sig);

    static List<(uint, uint, uint, string, bool)> LoadMaps()
    {
        var list = new List<(uint, uint, uint, string, bool)>();
        foreach (var line in File.ReadAllLines($"/proc/{pid}/maps"))
        {
            var f = line.Split(' ', StringSplitOptions.RemoveEmptyEntries);
            var range = f[0].Split('-');
            list.Add((Convert.ToUInt32(range[0], 16), Convert.ToUInt32(range[1], 16),
                      Convert.ToUInt32(f[2], 16), f.Length > 5 ? f[5] : "", f[1][2] == 'x'));
        }
        return list;
    }
}
