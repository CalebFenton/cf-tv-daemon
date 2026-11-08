using System;
using System.IO;
using System.Runtime.InteropServices;

/// <summary>
/// Asks the capture library directly why it refuses us.
///
/// libvideo-capture-impl-sec.so exports scaler_capture_is_protect(int* out),
/// which opens /dev/dri/card1 and issues ioctl 0xc0086461. A non-zero result
/// makes the library route through TZCAPTURE, whose checkProcessWhitelist then
/// rejects any binary that is not on its 13-entry list.
///
/// So: non-zero here == "we are being sent down the protected path".
/// </summary>
internal static class Program
{
    [DllImport("libvideo-capture-impl-sec.so", EntryPoint = "scaler_capture_is_protect")]
    private static extern int IsProtect(out int value);

    [DllImport("libvideo-capture-impl-sec.so", EntryPoint = "scaler_init")]
    private static extern int ScalerInit(out int handle);

    private static void Main()
    {
        Console.WriteLine("uid=" + Environment.UserName);

        try
        {
            int handle = -1;
            int rcInit = ScalerInit(out handle);
            Console.WriteLine($"scaler_init rc={rcInit} handle=0x{handle:x}");
        }
        catch (Exception ex) { Console.WriteLine("scaler_init EX " + ex.GetType().Name + ": " + ex.Message); }

        try
        {
            int value = -1;
            int rc = IsProtect(out value);
            Console.WriteLine($"is_protect rc={rc} value={value}  => {(value != 0 ? "PROTECTED (TZCAPTURE path)" : "normal (scaler path)")}");
        }
        catch (Exception ex) { Console.WriteLine("is_protect EX " + ex.GetType().Name + ": " + ex.Message); }

        // Also report what the whitelist would compare us against.
        try
        {
            byte[] cmdline = File.ReadAllBytes("/proc/self/cmdline");
            Console.WriteLine("cmdline=" + System.Text.Encoding.ASCII.GetString(cmdline).Replace('\0', ' ').Trim());
        }
        catch (Exception ex) { Console.WriteLine("cmdline EX " + ex.Message); }
    }
}
