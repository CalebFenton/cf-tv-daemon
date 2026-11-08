using System;
using System.Collections.Generic;
using System.IO;
using System.Net;
using System.Net.Sockets;
using System.Runtime.InteropServices;
using System.Security.Cryptography;
using System.Text;
using System.Threading;

/// <summary>
/// Screen capture for this TV, through Samsung's libep-common-screencapture.
///
/// `EPScreenCapture::CaptureScreen` builds a `ScreenCapture` (720x404 at
/// most), fills it with `IVideoCapture::getInstance()`'s first method, and
/// JPEG-encodes it to a file with `saveTo`. This probe makes the same calls
/// itself so that it can pick the method:
///   slot 0 (what CaptureScreen uses): every plane composited, including
///          app graphics, the OSD and our own windows (the HUD);
///   slot 1 (what libvideo-capture's secvideo_api_capture_screen_video_only
///          uses): the hardware video plane alone.
/// The plain GL grab (enlightenment_info -dump_screen) cannot see the video
/// plane at all.
///
/// `saveTo` takes a libstdc++ (C++11 ABI) std::string, 32-bit layout
///   char* data; size_t len; union { char buf[16]; size_t cap; };
/// filled in its long form (data on our heap, cap > 15), so no libstdc++
/// allocator is needed.
///
/// Usage:
///   CaptureProbe <out.jpg>                 one frame, every plane
///   CaptureProbe <out.jpg> --loop <n> <ms> n frames, <ms> apart, every plane
///   CaptureProbe --serve <port> [idle-s]   stay running; video-plane frames over TCP
///   CaptureProbe --probe                   just report the format
///
/// --serve is the HUD's frame source: the video plane only, so the HUD does
/// not see itself. It avoids a process start and a shell round trip per
/// frame. One client at a time. It sends the token from the CF_CAPTURE_TOKEN
/// environment variable and "\n", then one byte per frame wanted; each answer
/// is a 4-byte big-endian length and the JPEG (length 0: the capture failed).
/// Writes its pid to /tmp/cf-capture.pid; exits after idle-s seconds
/// (default 30) without a request.
/// </summary>
internal static class Program
{
    [StructLayout(LayoutKind.Sequential, Pack = 4)]
    private struct StdString
    {
        public IntPtr data;
        public uint len;
        public uint cap;
        public uint w1, w2, w3;
    }

    private const string ScreenCaptureLib = "libep-common-screencapture.so";

    [DllImport(ScreenCaptureLib, EntryPoint = "_ZN15EPScreenCapture13ScreenCaptureC1Ejj")]
    private static extern void ScreenCaptureNew(IntPtr self, uint width, uint height);

    [DllImport(ScreenCaptureLib, EntryPoint = "_ZN15EPScreenCapture13ScreenCaptureD1Ev")]
    private static extern void ScreenCaptureDelete(IntPtr self);

    [DllImport(ScreenCaptureLib, EntryPoint =
        "_ZN15EPScreenCapture13ScreenCapture6saveToERKNSt7__cxx1112basic_stringIcSt11char_traitsIcESaIcEEE")]
    private static extern int ScreenCaptureSaveTo(IntPtr self, ref StdString path);

    [DllImport("libvideo-capture.so.0", EntryPoint = "_ZN13IVideoCapture11getInstanceEv")]
    private static extern IntPtr VideoCaptureInstance();

    /// An IVideoCapture method: (this, input parameters, output parameters);
    /// a ScreenCapture holds both, the output 0x28 bytes in.
    [UnmanagedFunctionPointer(CallingConvention.Cdecl)]
    private delegate int VideoCaptureMethod(IntPtr capture, IntPtr input, IntPtr output);

    private const int EveryPlane = 0, VideoPlane = 1;  // IVideoCapture vtable slots
    private const int ScreenCaptureSize = 0x400;  // CaptureScreen keeps one in a 0x2c0-byte stack frame

    private const int SolSocket = 1, SoReuseAddr = 2;  // Linux values

    [DllImport("libc", SetLastError = true)]
    private static extern int setsockopt(int fd, int level, int name, ref int value, int length);

    private static int Main(string[] args)
    {
        if (args.Length == 0)
        {
            Console.Error.WriteLine("usage: CaptureProbe <out.jpg> [--loop n ms]");
            return 2;
        }

        if (args[0] == "--probe")
            return Probe();
        if (args[0] == "--serve" && args.Length >= 2)
            return Serve(int.Parse(args[1]), args.Length >= 3 ? int.Parse(args[2]) : 30);

        string outPath = args[0];
        Directory.CreateDirectory(Path.GetDirectoryName(outPath) ?? "/tmp");

        if (args.Length >= 4 && args[1] == "--loop")
        {
            int count = int.Parse(args[2]);
            int delay = int.Parse(args[3]);
            return Loop(outPath, count, delay);
        }

        return One(outPath);
    }

    private static int One(string outPath)
    {
        int rc = Grab(outPath, EveryPlane, out long size);
        if (rc != 1) { Console.Error.WriteLine("FAIL rc=" + rc); return 1; }
        Console.WriteLine("OK " + outPath + " " + size + " bytes");
        return 0;
    }

    private static int Loop(string outPath, int count, int delayMs)
    {
        string dir = Path.GetDirectoryName(outPath) ?? "/tmp";
        string stem = Path.GetFileNameWithoutExtension(outPath);
        string ext = Path.GetExtension(outPath);

        int ok = 0;
        for (int i = 0; i < count; i++)
        {
            string p = Path.Combine(dir, stem + "_" + i.ToString("0000") + ext);
            int rc = Grab(p, EveryPlane, out long size);
            if (rc == 1) { ok++; Console.WriteLine("OK " + p + " " + size); }
            else Console.Error.WriteLine("FAIL " + p + " rc=" + rc);
            if (i + 1 < count) Thread.Sleep(delayMs);
        }
        Console.WriteLine("done " + ok + "/" + count);
        return ok == count ? 0 : 1;
    }

    private static int Grab(string outPath, int slot, out long size)
    {
        size = 0;
        try
        {
            if (File.Exists(outPath)) File.Delete(outPath);

            IntPtr instance = VideoCaptureInstance();
            var method = Marshal.GetDelegateForFunctionPointer<VideoCaptureMethod>(
                Marshal.ReadIntPtr(Marshal.ReadIntPtr(instance), slot * IntPtr.Size));
            byte[] bytes = System.Text.Encoding.ASCII.GetBytes(outPath);
            IntPtr heap = Marshal.AllocHGlobal(bytes.Length + 1);
            IntPtr capture = Marshal.AllocHGlobal(ScreenCaptureSize);
            try
            {
                Marshal.Copy(bytes, 0, heap, bytes.Length);
                Marshal.WriteByte(heap, bytes.Length, 0);
                var path = new StdString { data = heap, len = (uint)bytes.Length, cap = (uint)bytes.Length + 1 };

                ScreenCaptureNew(capture, 720, 404);
                int rc;
                try
                {
                    if (method(instance, capture, capture + 0x28) < 0) return 0;
                    rc = ScreenCaptureSaveTo(capture, ref path);
                }
                finally { ScreenCaptureDelete(capture); }
                if (rc != 1) return rc;

                var fi = new FileInfo(outPath);
                if (!fi.Exists) return -1;
                size = fi.Length;

                // Sanity: JPEG magic.
                using (var fs = File.OpenRead(outPath))
                {
                    if (fs.ReadByte() != 0xFF || fs.ReadByte() != 0xD8) return -2;
                }
                return 1;
            }
            finally { Marshal.FreeHGlobal(heap); Marshal.FreeHGlobal(capture); }
        }
        catch (Exception ex)
        {
            Console.Error.WriteLine("EX " + ex.GetType().Name + ": " + ex.Message);
            return -3;
        }
    }

    private static int Serve(int port, int idleSeconds)
    {
        string token = Environment.GetEnvironmentVariable("CF_CAPTURE_TOKEN");
        if (string.IsNullOrEmpty(token)) { Console.Error.WriteLine("CF_CAPTURE_TOKEN is not set"); return 2; }
        const string frame = "/tmp/cf-capture.jpg";
        var listener = new TcpListener(IPAddress.Any, port);
        // SO_REUSEADDR alone, so a restart can bind while old connections sit
        // in TIME_WAIT. .NET's ReuseAddress also sets SO_REUSEPORT on Linux,
        // which let two servers listen at once and split the connections.
        int on = 1;
        setsockopt((int)listener.Server.Handle, SolSocket, SoReuseAddr, ref on, sizeof(int));
        listener.Start();
        File.WriteAllText("/tmp/cf-capture.pid", System.Diagnostics.Process.GetCurrentProcess().Id.ToString());
        var idle = System.Diagnostics.Stopwatch.StartNew();
        while (idle.Elapsed.TotalSeconds < idleSeconds)
        {
            if (!listener.Pending()) { Thread.Sleep(20); continue; }
            using (TcpClient client = listener.AcceptTcpClient())
            using (NetworkStream stream = client.GetStream())
            {
                client.NoDelay = true;
                stream.ReadTimeout = idleSeconds * 1000;
                try
                {
                    if (!CryptographicOperations.FixedTimeEquals(ReadLine(stream), Encoding.ASCII.GetBytes(token)))
                        continue;
                    while (stream.ReadByte() >= 0)
                    {
                        byte[] jpeg = Grab(frame, VideoPlane, out _) == 1 ? File.ReadAllBytes(frame) : new byte[0];
                        byte[] length = BitConverter.GetBytes(IPAddress.HostToNetworkOrder(jpeg.Length));
                        stream.Write(length, 0, 4);
                        stream.Write(jpeg, 0, jpeg.Length);
                    }
                }
                catch (IOException) { }  // idle timeout, or the client went away
            }
            idle.Restart();
        }
        return 0;
    }

    /// Bytes up to "\n", unbuffered: what follows are frame requests.
    private static byte[] ReadLine(NetworkStream stream)
    {
        var line = new List<byte>();
        for (int b = stream.ReadByte(); b >= 0 && b != '\n' && line.Count < 256; b = stream.ReadByte()) line.Add((byte)b);
        return line.ToArray();
    }

    private static int Probe()
    {
        string p = "/tmp/cap/_probe.jpg";
        Directory.CreateDirectory("/tmp/cap");
        int rc = Grab(p, EveryPlane, out long size);
        if (rc != 1) { Console.Error.WriteLine("probe FAIL rc=" + rc); return 1; }

        var dims = JpegSize(p);
        Console.WriteLine("rc=1 size=" + size + " dims=" + dims);
        return 0;
    }

    /// Minimal JPEG SOF0 dimension reader.
    private static string JpegSize(string path)
    {
        byte[] d = File.ReadAllBytes(path);
        int i = 2;
        while (i + 9 < d.Length)
        {
            if (d[i] != 0xFF) { i++; continue; }
            byte m = d[i + 1];
            if (m >= 0xC0 && m <= 0xCF && m != 0xC4 && m != 0xC8 && m != 0xCC)
            {
                int h = (d[i + 5] << 8) | d[i + 6];
                int w = (d[i + 7] << 8) | d[i + 8];
                return w + "x" + h;
            }
            int len = (d[i + 2] << 8) | d[i + 3];
            i += 2 + len;
        }
        return "?";
    }
}
