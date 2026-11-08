using System;
using System.IO;
using System.Runtime.InteropServices;
using System.Threading;

class V
{
    [DllImport("libc.so.6")] static extern int getpid();
    [DllImport("libc.so.6")] static extern int getppid();

    static void Main(string[] a)
    {
        int pid = getpid();
        string ready = a.Length > 0 ? a[0] : "/tmp/victim.pid";
        File.WriteAllText(ready, pid.ToString());
        Console.WriteLine("victim pid=" + pid + " ready");
        Console.Out.Flush();
        // tight native-call loop: hits libc getpid()/getppid() each iteration
        long n = 0;
        while (true)
        {
            getpid();
            getppid();
            n++;
            if ((n % 2000000) == 0) Console.WriteLine("iter " + n);
        }
    }
}
