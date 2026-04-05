#define MyAppName "Минимальная канбан-доска"
#define MyAppVersion "0.2.0"
#ifndef SourceDir
  #define SourceDir "..\\dist\\MinimalKanban"
#endif
#ifndef OutputDir
  #define OutputDir "..\\installer-output"
#endif

[Setup]
AppId={{7F23F490-89AF-4EAF-AE56-6A5A0BE42AA8}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher=Локальный MVP
DefaultDirName={localappdata}\Programs\Minimal Kanban
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
Compression=lzma
SolidCompression=yes
WizardStyle=modern
OutputDir={#OutputDir}
OutputBaseFilename=MinimalKanban-Setup
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayIcon={app}\MinimalKanban.exe
PrivilegesRequired=lowest

[Languages]
Name: "russian"; MessagesFile: "compiler:Languages\Russian.isl"

[Tasks]
Name: "desktopicon"; Description: "Создать ярлык на рабочем столе"; GroupDescription: "Дополнительные значки:"

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autodesktop}\Минимальная канбан-доска"; Filename: "{app}\MinimalKanban.exe"; Tasks: desktopicon
Name: "{group}\Минимальная канбан-доска"; Filename: "{app}\MinimalKanban.exe"
Name: "{group}\Удалить Минимальную канбан-доску"; Filename: "{uninstallexe}"

[Run]
Filename: "{app}\MinimalKanban.exe"; Description: "Запустить программу"; Flags: nowait postinstall skipifsilent
