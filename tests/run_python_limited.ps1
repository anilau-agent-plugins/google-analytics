param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]] $PythonArgs
)

$source = @'
using System;
using System.Runtime.InteropServices;

public static class GoogleAnalyticsStageJobLimit {
    [StructLayout(LayoutKind.Sequential)]
    private struct JOBOBJECT_BASIC_LIMIT_INFORMATION {
        public long PerProcessUserTimeLimit;
        public long PerJobUserTimeLimit;
        public uint LimitFlags;
        public UIntPtr MinimumWorkingSetSize;
        public UIntPtr MaximumWorkingSetSize;
        public uint ActiveProcessLimit;
        public UIntPtr Affinity;
        public uint PriorityClass;
        public uint SchedulingClass;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct IO_COUNTERS {
        public ulong ReadOperationCount;
        public ulong WriteOperationCount;
        public ulong OtherOperationCount;
        public ulong ReadTransferCount;
        public ulong WriteTransferCount;
        public ulong OtherTransferCount;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct JOBOBJECT_EXTENDED_LIMIT_INFORMATION {
        public JOBOBJECT_BASIC_LIMIT_INFORMATION BasicLimitInformation;
        public IO_COUNTERS IoInfo;
        public UIntPtr ProcessMemoryLimit;
        public UIntPtr JobMemoryLimit;
        public UIntPtr PeakProcessMemoryUsed;
        public UIntPtr PeakJobMemoryUsed;
    }

    [DllImport("kernel32.dll", CharSet = CharSet.Unicode)]
    private static extern IntPtr CreateJobObject(IntPtr attributes, string name);

    [DllImport("kernel32.dll")]
    private static extern bool SetInformationJobObject(IntPtr job, int infoClass, IntPtr info, uint length);

    [DllImport("kernel32.dll")]
    private static extern bool AssignProcessToJobObject(IntPtr job, IntPtr process);

    private static IntPtr job;

    public static void Assign(IntPtr process, ulong bytes) {
        job = CreateJobObject(IntPtr.Zero, null);
        if (job == IntPtr.Zero) throw new InvalidOperationException("CreateJobObject failed");
        var info = new JOBOBJECT_EXTENDED_LIMIT_INFORMATION();
        info.BasicLimitInformation.LimitFlags = 0x00002000u | 0x00000200u;
        info.JobMemoryLimit = new UIntPtr(bytes);
        int size = Marshal.SizeOf(info);
        IntPtr pointer = Marshal.AllocHGlobal(size);
        try {
            Marshal.StructureToPtr(info, pointer, false);
            if (!SetInformationJobObject(job, 9, pointer, (uint)size))
                throw new InvalidOperationException("SetInformationJobObject failed");
        } finally {
            Marshal.FreeHGlobal(pointer);
        }
        if (!AssignProcessToJobObject(job, process))
            throw new InvalidOperationException("AssignProcessToJobObject failed");
    }
}
'@

Add-Type -TypeDefinition $source
$start = [System.Diagnostics.ProcessStartInfo]::new()
$start.FileName = "python"
$start.UseShellExecute = $false
$start.WorkingDirectory = (Get-Location).Path
$start.Environment["PYTHONDONTWRITEBYTECODE"] = "1"
$start.ArgumentList.Add("-B")
foreach ($argument in $PythonArgs) {
    $start.ArgumentList.Add($argument)
}
$process = [System.Diagnostics.Process]::Start($start)
[GoogleAnalyticsStageJobLimit]::Assign($process.Handle, 536870912)
$process.WaitForExit()
exit $process.ExitCode
