function Assert-CiProfileCompletion {
    param(
        [Parameter(Mandatory = $true)]
        [bool]$Completed,
        [Parameter(Mandatory = $true)]
        [bool]$Failed
    )

    if (-not $Completed -and -not $Failed) {
        [Console]::Error.WriteLine("Local CI profile interrupted before completion.")
        exit 130
    }
}
