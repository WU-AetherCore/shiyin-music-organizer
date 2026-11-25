#define MyAppName "Shiyin Music Organizer"
#define MyAppVersion "1.3.39"
#define MyAppPublisher "Shiyin"
#define MyAppExeName "ShiyinMusicOrganizer.exe"

[Setup]
AppId={{6F5B4D6B-7E0D-4C77-9E04-0B67B8C9A101}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={localappdata}\Programs\ShiyinMusicOrganizer
DefaultGroupName=Shiyin Music Organizer
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=E:\WUFUDONG\Linux-Project\RaspberryPi5_Project\build\installer-output
OutputBaseFilename=ShiyinMusicOrganizer-Setup-{#MyAppVersion}
SetupIconFile=E:\WUFUDONG\Linux-Project\RaspberryPi5_Project\本地音乐整理小插件\desktop\app.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
CloseApplications=yes
CloseApplicationsFilter=ShiyinMusicOrganizer.exe
SetupLogging=yes

[Languages]
Name: "chinesesimp"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Shortcuts:"; Flags: unchecked

[Files]
Source: "E:\WUFUDONG\Linux-Project\RaspberryPi5_Project\build\shiyin-dist\ShiyinMusicOrganizer\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\Shiyin Music Organizer"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\{#MyAppExeName}"
Name: "{group}\Uninstall Shiyin Music Organizer"; Filename: "{uninstallexe}"
Name: "{autodesktop}\Shiyin Music Organizer"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Start Shiyin Music Organizer"; Flags: nowait postinstall skipifsilent
