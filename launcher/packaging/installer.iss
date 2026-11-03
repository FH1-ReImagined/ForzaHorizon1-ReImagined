; Inno Setup script for the Windows installer. Built by .github/workflows/release.yml:
;   iscc /DAppVersion=1.2.3 /DVersionNumeric=1.2.3.0 /DStage=<folder from make_package.py> /DRedist=<vc_redist.x64.exe> /DOut=<dir> installer.iss
; VersionNumeric is the four-part number of AppVersion (python launcher/packaging/windows_version_info.py 1.2.3).
; It installs the launcher for the current user (no administrator rights), and the Microsoft Visual
; C++ 2015-2022 x64 redistributable when it is missing, after a page that says so. It shows the privacy
; policy (PRIVACY.md) and an "Online features" option (#497); the launcher reads that choice from
; install_options.json next to forza-launcher.exe (launcher/forza_launcher/online_features.py).
; A silent install takes the option from the command line: /ONLINE=no turns the features off.
; The installer contains no game code or data.
#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#ifndef VersionNumeric
  #define VersionNumeric "0.0.0.0"
#endif
#define AppName "Forza Horizon 1: ReImagined"
; The publisher and copyright text of forza-launcher.exe's version resource are the same (windows_version_info.py).
#define Publisher "Dyotson"
#define LegalCopyright "Copyright (c) 2026 Dyotson and Forza Horizon 1: ReImagined contributors"
; Folder and shortcut names: Windows does not allow ":" in them.
#define ShortName "Forza Horizon 1 - ReImagined"
; The "Online features" choice for the launcher (online_features.SEED_NAME).
#define SeedName "install_options.json"

[Setup]
AppId={{B0C6F2A4-5D1E-4D0B-9B1B-7B5A0F3E6C21}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#Publisher}
AppPublisherURL=https://github.com/FH1-ReImagined/ForzaHorizon1-ReImagined
DefaultDirName={localappdata}\Programs\ForzaHorizon1-ReImagined
DefaultGroupName={#ShortName}
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir={#Out}
OutputBaseFilename=ForzaHorizon1-ReImagined-Setup-{#AppVersion}
; The installer's own Windows version resource, so Explorer, Task Manager and antivirus reports name it
; and its version (#482, #354). The numeric version cannot hold a development tag, so the text one is AppVersion.
VersionInfoVersion={#VersionNumeric}
VersionInfoProductVersion={#VersionNumeric}
VersionInfoProductTextVersion={#AppVersion}
VersionInfoCompany={#Publisher}
VersionInfoProductName={#AppName}
VersionInfoDescription={#AppName} Setup
VersionInfoCopyright={#LegalCopyright}
; Inno's OriginalFileName slot holds 50 characters. A development version in this
; field truncates even the .exe suffix; keep the version in the fields above and
; the download name (OutputBaseFilename), and use the stable executable name here.
VersionInfoOriginalFileName=ForzaHorizon1-ReImagined-Setup.exe
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
SetupIconFile=../../forza-recomp/resources/forza.ico
UninstallDisplayIcon={app}\forza-launcher.exe
LicenseFile={#Stage}\licenses\ForzaHorizon1-ReImagined.txt
; The privacy policy (make_package.py copies PRIVACY.md here), right after the licence and before any
; choice; the "Online features" page follows it.
InfoBeforeFile={#Stage}\licenses\PRIVACY.txt
DisableProgramGroupPage=yes
; Always show where the files go and let the player change it, also on updates (Inno Setup's
; default skips the page once a previous install exists, issue #403). The launcher's own update
; passes /DIR, so the page comes up filled with the current install folder.
DisableDirPage=no
; Installed files keep the build's real (UTC) file times. By default Inno Setup stores the build machine's
; local time (UTC on CI) and sets it as the player's local time: hours in the future west of UTC, which
; keeps ninja re-running CMake (issue #35).
TimeStampsInUTC=yes

[Messages]
WizardInfoBefore=Privacy policy
InfoBeforeLabel=Please read how the launcher and the game use the network.

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Shortcuts:"

[Files]
Source: "{#Stage}\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion
#ifdef Redist
Source: "{#Redist}"; DestDir: "{tmp}"; Flags: deleteafterinstall; Check: NeedsVCRedist
#endif

[Icons]
Name: "{autoprograms}\{#ShortName}"; Filename: "{app}\forza-launcher.exe"
Name: "{autodesktop}\{#ShortName}"; Filename: "{app}\forza-launcher.exe"; Tasks: desktopicon

[Run]
#ifdef Redist
Filename: "{tmp}\vc_redist.x64.exe"; Parameters: "/install /quiet /norestart"; StatusMsg: "Installing the Microsoft Visual C++ runtime..."; Flags: shellexec waituntilterminated; Check: NeedsVCRedist; AfterInstall: WarnIfVCRedistMissing
#endif
Filename: "{app}\forza-launcher.exe"; Description: "Start the launcher"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
Type: files; Name: "{app}\{#SeedName}"

[Code]
var
  OnlinePage: TInputOptionWizardPage;
#ifdef Redist
  RuntimePage: TOutputMsgWizardPage;
#endif

function NeedsVCRedist: Boolean;
var
  Installed: Cardinal;
begin
  Result := True;
  if RegQueryDWordValue(HKLM64, 'SOFTWARE\Microsoft\VisualStudio\14.0\VC\Runtimes\x64', 'Installed', Installed) then
    Result := Installed <> 1;
end;

procedure InitializeWizard;
begin
  // Ticked by default; untick (or /ONLINE=no on the command line) to turn every feature off.
  OnlinePage := CreateInputOptionPage(wpInfoBefore,
    'Online features', 'Choose whether the launcher and the game use the network on their own.',
    'When on: online play on the project''s public Forza Live server; after you play, shader lists shared ' +
    'with that server and your co-op campaign results fetched from it; a check for launcher updates on ' +
    'GitHub when the launcher opens; and your game activity shown in the Discord app.' + #13#10#13#10 +
    'Untick to turn all of them off. You can turn each one on or off later in the launcher. On an ' +
    'update, leaving this ticked keeps your current launcher settings.',
    False, False);
  OnlinePage.Add('Turn on online features');
  OnlinePage.Values[0] := CompareText(ExpandConstant('{param:ONLINE|yes}'), 'no') <> 0;
#ifdef Redist
  // A system change: say so before it happens (shown only when the runtime is missing).
  RuntimePage := CreateOutputMsgPage(wpSelectTasks,
    'Microsoft Visual C++ runtime', 'Setup will change this PC''s system configuration.',
    'The game needs the Microsoft Visual C++ 2015-2022 x64 runtime, which this PC does not have. ' +
    'Setup will install it for all users of this PC, and Windows will ask for administrator approval.' + #13#10#13#10 +
    'Uninstalling Forza Horizon 1: ReImagined leaves the runtime installed, because other programs ' +
    'can use it too.' + #13#10#13#10 +
    'Click Next to continue, or Cancel to leave Setup without changing anything.');
#endif
end;

function ShouldSkipPage(PageID: Integer): Boolean;
begin
  Result := False;
#ifdef Redist
  if PageID = RuntimePage.ID then
    Result := not NeedsVCRedist;
#endif
end;

procedure AddMemo(var Memo: String; const Section, NewLine: String);
begin
  if Section <> '' then
  begin
    if Memo <> '' then
      Memo := Memo + NewLine + NewLine;
    Memo := Memo + Section;
  end;
end;

function UpdateReadyMemo(Space, NewLine, MemoUserInfoInfo, MemoDirInfo, MemoTypeInfo,
  MemoComponentsInfo, MemoGroupInfo, MemoTasksInfo: String): String;
var
  Online: String;
begin
  Result := '';
  AddMemo(Result, MemoUserInfoInfo, NewLine);
  AddMemo(Result, MemoDirInfo, NewLine);
  AddMemo(Result, MemoTypeInfo, NewLine);
  AddMemo(Result, MemoComponentsInfo, NewLine);
  AddMemo(Result, MemoGroupInfo, NewLine);
  AddMemo(Result, MemoTasksInfo, NewLine);
  if OnlinePage.Values[0] then
    Online := 'On. You can turn each one off later in the launcher.'
  else
    Online := 'Off. The game plays offline; the launcher sends nothing on its own.';
  AddMemo(Result, 'Online features:' + NewLine + Space + Online, NewLine);
#ifdef Redist
  if NeedsVCRedist then
    AddMemo(Result, 'Changes to Windows:' + NewLine + Space + 'Install the Microsoft Visual C++ 2015-2022 x64 runtime' +
      NewLine + Space + 'for all users of this PC (Windows asks for administrator approval).', NewLine);
#endif
end;

// After vc_redist ran: a declined administrator prompt or a failure would otherwise go unnoticed
// (Inno Setup does not check the exit code of a shellexec entry).
procedure WarnIfVCRedistMissing;
begin
  if NeedsVCRedist then
  begin
    Log('The Microsoft Visual C++ runtime is still missing after its installer ran.');
    if not WizardSilent then
      MsgBox('The Microsoft Visual C++ runtime was not installed: administrator approval was declined, ' +
        'or its installer failed. The game cannot start without it. Run this Setup again, or install it ' +
        'from Microsoft: https://aka.ms/vs/17/release/vc_redist.x64.exe', mbError, MB_OK);
  end;
end;

// The "Online features" choice for the launcher, with a stamp of this run: the launcher applies each
// run's choice once (to a new launcher configuration, or "off" to an existing one).
procedure CurStepChanged(CurStep: TSetupStep);
var
  Online: String;
begin
  if CurStep = ssPostInstall then
  begin
    if OnlinePage.Values[0] then
      Online := 'true'
    else
      Online := 'false';
    if not SaveStringToFile(ExpandConstant('{app}\{#SeedName}'), '{"online_features": ' + Online +
        ', "stamp": "' + GetDateTimeString('yyyymmddhhnnss', #0, #0) + '"}' + #13#10, False) then
      Log('Could not write {#SeedName}');
  end;
end;
