; Script Inno Setup cho CapCap
; Ho tro: Chon thu muc cai dat, tao Desktop icon, tu dong cai Microsoft Visual C++ Redistributable x64 (kiem tra phien ban >= 14.20)

#define MyAppName "CapCap"
#define MyAppVersion "8.0.1"
#define MyAppPublisher "CapCap"
#define MyAppURL "https://github.com/notepower2k1/CapCap"
#define MyAppExeName "CapCap.exe"

[Setup]
AppId={{8B1B6D24-4B44-48FE-9D07-1F4F9A7B6E21}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
AppUpdatesURL={#MyAppURL}
DefaultDirName={autopf}\{#MyAppName}
DisableDirPage=no
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=no
AllowNoIcons=yes
OutputDir=..\dist_installer
OutputBaseFilename=CapCap_Setup
SetupIconFile=..\assets\capcap.ico
Compression=lzma2/normal
SolidCompression=yes
WizardStyle=modern
ArchitecturesInstallIn64BitMode=x64compatible
ArchitecturesAllowed=x64compatible
PrivilegesRequired=admin

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Dirs]
Name: "{app}"; Permissions: users-modify

[Files]
; Bo source ung dung tu PyInstaller dist
Source: "..\dist\CapCap\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

; Microsoft Visual C++ 2015-2022 Redistributable (x64)
Source: "vc_redist.x64.exe"; DestDir: "{tmp}"; Flags: ignoreversion deleteafterinstall; Check: not IsVCRedistSufficient

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\{#MyAppExeName}"
Name: "{group}\{cm:UninstallProgram,{#MyAppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon; IconFilename: "{app}\{#MyAppExeName}"

[Run]
; Tu dong cai ngam Visual C++ Redistributable neu may nguoi dung chua co
Filename: "{tmp}\vc_redist.x64.exe"; Parameters: "/install /quiet /norestart"; Flags: waituntilterminated; Check: not IsVCRedistSufficient; StatusMsg: "Installing Microsoft Visual C++ Redistributable (x64)..."

; Khoi dong ung dung sau khi cai dat xong
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent

[Code]
// Check VC++ 2015-2022 Redistributable x64 is installed AND at minimum version 14.20
// libmpv-2.dll requires VC++ runtime >= 14.20 (VS 2019+)
function IsVCRedistSufficient: Boolean;
var
  Installed: Cardinal;
  Minor: Cardinal;
  RootKey: Integer;
  RegPath: String;
begin
  Result := False;
  RegPath := 'SOFTWARE\Microsoft\VisualStudio\14.0\VC\Runtimes\X64';

  // Try 64-bit registry first, then 32-bit view
  if not RegQueryDWordValue(HKLM64, RegPath, 'Installed', Installed) then
    if not RegQueryDWordValue(HKLM, RegPath, 'Installed', Installed) then
      Exit; // Key doesn't exist at all - not installed

  if Installed <> 1 then
    Exit; // Marked as not installed

  // Check minor version: require >= 20 (VS 2019 = 14.20, VS 2022 = 14.30+)
  if RegQueryDWordValue(HKLM64, RegPath, 'Minor', Minor) or
     RegQueryDWordValue(HKLM, RegPath, 'Minor', Minor) then
  begin
    if Minor >= 20 then
      Result := True;
    // else: installed but too old (14.0.x from VS 2015) - return False to trigger re-install
  end;
  // If Minor key missing, assume it's old enough to need upgrade
end;
