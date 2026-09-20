; Inno Setup script for FinanceRanker.
;
; Builds a Windows installer (.exe) around the PyInstaller output in dist\FinanceRanker.
;
; Prereq: build the app FIRST with PyInstaller, then compile this:
;     pyinstaller --clean --noconfirm finance_ranker.spec
;     iscc windows\installer.iss
;
; PyInstaller cannot cross-compile, so both steps must run on Windows.
; .github/workflows/build.yml does exactly that on a windows-latest runner.

#define MyAppName "FinanceRanker"
#define MyAppVersion "0.1.1"
#define MyAppPublisher "FinanceRanker"
#define MyAppExeName "FinanceRanker.exe"
; Relative to this .iss file, so the script works from any checkout location.
#define DistDir "..\dist\FinanceRanker"

[Setup]
AppId={{7C4E1B2A-9D3F-4A61-B0E7-2F5A8C1D4E93}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
; Per-user install by default so there is no UAC prompt, with the option to
; switch to an all-users install from the dialog.
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
AllowNoIcons=yes
; The bundle is architecture-bound (x64) and needs a modern OS.
MinVersion=10.0.17763
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..\dist
OutputBaseFilename=FinanceRanker-{#MyAppVersion}-setup
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
UninstallDisplayIcon={app}\{#MyAppExeName}
SetupIconFile=..\app\web\static\FinanceRanker.ico

[Languages]
; Chinese first: this is a Chinese-language tool.
;
; The translation is bundled next to this script rather than taken from the
; compiler's Languages directory, because that directory does not reliably
; contain it (the chocolatey Inno Setup 6.7.1 used by CI does not) and a
; reference to a missing file aborts the whole compile. Preprocessor guards
; fall back to the compiler's own copy, then to English-only, so the installer
; still builds on a compiler that ships neither.
#define BundledChineseIsl SourcePath + "\ChineseSimplified.isl"
#define CompilerChineseIsl CompilerPath + "\Languages\ChineseSimplified.isl"
#if FileExists(BundledChineseIsl)
Name: "chinesesimplified"; MessagesFile: "{#BundledChineseIsl}"
#else
#if FileExists(CompilerChineseIsl)
Name: "chinesesimplified"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"
#endif
#endif
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
; The whole PyInstaller output tree: the .exe, _internal\, and -- on Windows --
; the bundled Chromium under _internal\chromium\. Nothing is downloaded at
; install time any more: the app carries its own browser, so it needs neither
; WebView2 nor an existing Edge/Chrome, and the installer stays fully offline.
Source: "{#DistDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\{cm:UninstallProgram,{#MyAppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#MyAppName}}"; Flags: nowait postinstall skipifsilent
