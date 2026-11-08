using System;
using System.Runtime.InteropServices;

/// <summary>
/// Minimal chattr for the TV, which ships neither chattr nor a toybox applet.
///
///   dotnet Chattr.dll get  PATH...   print the inode flags (hex) of each path
///   dotnet Chattr.dll +i   PATH...   set   FS_IMMUTABLE_FL
///   dotnet Chattr.dll -i   PATH...   clear FS_IMMUTABLE_FL
///
/// An immutable file cannot be written, renamed over, or deleted, even through
/// a world-writable directory; only a process with CAP_LINUX_IMMUTABLE (root)
/// can clear the flag. Exit status: 0 if every path succeeded.
/// </summary>
internal static class Program
{
    // ARM32: _IOR('f', 1, long) and _IOW('f', 2, long).
    private const uint FS_IOC_GETFLAGS = 0x80046601;
    private const uint FS_IOC_SETFLAGS = 0x40046602;
    private const int FS_IMMUTABLE_FL = 0x10;
    private const int O_RDONLY = 0, O_NONBLOCK = 0x800;

    [DllImport("libc", SetLastError = true)]
    private static extern int open(string path, int flags);

    [DllImport("libc", SetLastError = true)]
    private static extern int ioctl(int fd, uint request, ref int argument);

    [DllImport("libc")]
    private static extern int close(int fd);

    private static int Main(string[] args)
    {
        if (args.Length < 2 || (args[0] != "get" && args[0] != "+i" && args[0] != "-i"))
        {
            Console.Error.WriteLine("usage: Chattr get|+i|-i PATH...");
            return 2;
        }
        int failures = 0;
        for (int i = 1; i < args.Length; i++)
        {
            string path = args[i];
            int fd = open(path, O_RDONLY | O_NONBLOCK);
            if (fd < 0)
            {
                Console.Error.WriteLine($"{path}: open errno {Marshal.GetLastWin32Error()}");
                failures++;
                continue;
            }
            int flags = 0;
            if (ioctl(fd, FS_IOC_GETFLAGS, ref flags) != 0)
            {
                Console.Error.WriteLine($"{path}: GETFLAGS errno {Marshal.GetLastWin32Error()}");
                failures++;
            }
            else if (args[0] == "get")
            {
                Console.WriteLine($"0x{flags:x8} {((flags & FS_IMMUTABLE_FL) != 0 ? "i" : "-")} {path}");
            }
            else
            {
                flags = args[0] == "+i" ? flags | FS_IMMUTABLE_FL : flags & ~FS_IMMUTABLE_FL;
                if (ioctl(fd, FS_IOC_SETFLAGS, ref flags) != 0)
                {
                    Console.Error.WriteLine($"{path}: SETFLAGS errno {Marshal.GetLastWin32Error()}");
                    failures++;
                }
            }
            close(fd);
        }
        return failures == 0 ? 0 : 1;
    }
}
