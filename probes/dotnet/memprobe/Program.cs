using System;
using System.IO;
using System.Runtime.InteropServices;

class P
{
    [DllImport("libc.so.6", SetLastError=true)]
    static extern int open(string path, int flags);
    [DllImport("libc.so.6", SetLastError=true)]
    static extern int close(int fd);
    [DllImport("libc.so.6", SetLastError=true)]
    static extern IntPtr pread(int fd, byte[] buf, IntPtr count, IntPtr offset);
    [DllImport("libc.so.6", SetLastError=true)]
    static extern IntPtr pwrite(int fd, byte[] buf, IntPtr count, IntPtr offset);

    static void Main(string[] a)
    {
        // usage: memprobe <pid> <hexaddr> <mode>   mode: rd|wr|rw
        if (a.Length < 3) { Console.WriteLine("usage: memprobe <pid> <hexaddr> <rd|wr|rw>"); return; }
        int pid = int.Parse(a[0]);
        long addr = Convert.ToInt64(a[1], 16);
        string mode = a[2];
        int flags = mode == "rd" ? 0 : (mode == "wr" ? 1 : 2);
        string path = "/proc/" + pid + "/mem";
        int fd = open(path, flags);
        if (fd < 0)
        {
            Console.WriteLine($"OPEN FAILED {mode} on {path} errno={Marshal.GetLastWin32Error()}");
            return;
        }
        Console.WriteLine($"open {mode} OK on {path}");
        var buf = new byte[16];
        var r = pread(fd, buf, (IntPtr)16, (IntPtr)addr);
        Console.WriteLine("  pread  -> " + (long)r + " errno=" + Marshal.GetLastWin32Error() + " " + BitConverter.ToString(buf));
        var w = new byte[16]; for (int i=0;i<16;i++) w[i]=0xAA;
        var wr = pwrite(fd, w, (IntPtr)16, (IntPtr)addr);
        Console.WriteLine("  pwrite -> " + (long)wr + " errno=" + Marshal.GetLastWin32Error());
        close(fd);
    }
}
