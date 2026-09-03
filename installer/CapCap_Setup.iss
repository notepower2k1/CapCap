; Script Inno Setup cho CapCap
; Ho tro: Chon thu muc cai dat, tao Desktop icon, tu dong cai Microsoft Visual C++ Redistributable x64

#define MyAppName "CapCap"
#define MyAppVersion "1.0.0"
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
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
ArchitecturesInstallIn64BitMode=x64compatible
ArchitecturesAllowed=x64compatible
PrivilegesRequired=admin

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
; Bo source ung dung tu PyInstaller dist
Source: "..\dist\CapCap\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

; Microsoft Visual C++ 2015-2022 Redistributable (x64)
Source: "vc_redist.x64.exe"; DestDir: "{tmp}"; Flags: ignoreversion deleteafterinstall; Check: not IsVCRedistInstalled

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\assets\capcap.ico"
Name: "{group}\{cm:UninstallProgram,{#MyAppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon; IconFilename: "{app}\assets\capcap.ico"

[Run]
; Tu dong cai ngam Visual C++ Redistributable neu may nguoi dung chua co
Filename: "{tmp}\vc_redist.x64.exe"; Parameters: "/install /quiet /norestart"; Flags: waituntilterminated; Check: not IsVCRedistInstalled; StatusMsg: "Installing Microsoft Visual C++ Redistributable (x64)..."

; Khoi dong ung dung sau khi cai dat xong
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent

[Code]
// Kiem tra xem may da cai dat Microsoft Visual C++ 2015-2022 Redistributable (x64) chua
function IsVCRedistInstalled: Boolean;
var
  Installed: Cardinal;
begin
  Result := False;
  if RegQueryDWordValue(HKLM64, 'SOFTWARE\Microsoft\VisualStudio\14.0\VC\Runtimes\X64', 'Installed', Installed) then
  begin
    if Installed = 1 then
      Result := True;
  end
  else if RegQueryDWordValue(HKLM, 'SOFTWARE\Microsoft\VisualStudio\14.0\VC\Runtimes\X64', 'Installed', Installed) then
  begin
    if Installed = 1 then
      Result := True;
  end;
end;
