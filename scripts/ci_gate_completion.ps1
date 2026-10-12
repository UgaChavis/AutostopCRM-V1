function Initialize-CiProfileCompletion {
    if (-not ("AutoStop.Ci.CompletionGuard" -as [type])) {
        # Ctrl+C can skip PowerShell command calls even inside finally.
        # A native process-exit handler still rejects an incomplete run.
        Add-Type -TypeDefinition @'
using System;
using System.Threading;

namespace AutoStop.Ci {
    public static class CompletionGuard {
        private static int pending;
        private static int reported;
        private const string Interrupted = "Local CI profile interrupted before completion.";

        static CompletionGuard() {
            AppDomain.CurrentDomain.ProcessExit += (sender, args) => {
                if (Interlocked.Exchange(ref pending, 0) == 0) return;
                if (Volatile.Read(ref reported) == 0) Console.Error.WriteLine(Interrupted);
                Environment.ExitCode = 130;
            };
        }

        public static void Begin() {
            Volatile.Write(ref reported, 0);
            Volatile.Write(ref pending, 1);
        }

        public static void Finish(bool completed, bool failed) {
            if (completed || failed) {
                Volatile.Write(ref pending, 0);
            } else {
                Console.Error.WriteLine(Interrupted);
                Volatile.Write(ref reported, 1);
            }
        }
    }
}
'@
    }
    [AutoStop.Ci.CompletionGuard]::Begin()
}

function Assert-CiProfileCompletion {
    param(
        [Parameter(Mandatory = $true)]
        [bool]$Completed,
        [Parameter(Mandatory = $true)]
        [bool]$Failed
    )

    [AutoStop.Ci.CompletionGuard]::Finish($Completed, $Failed)
    if (-not $Completed -and -not $Failed) {
        exit 130
    }
}
