[Setup]
; NOTE: The value of AppId uniquely identifies this application. Do not use the same AppId value in installers for other applications.
; (To generate a new GUID, click Tools | Generate GUID inside the IDE.)
AppId={{8A3B2C1D-4E5F-6A7B-8C9D-0E1F2A3B4C5D}
AppName=NSP Cosmetic POS
AppVersion=1.0
AppPublisher=NSP
DefaultDirName={autopf}\NSP Cosmetic POS
DefaultGroupName=NSP Cosmetic POS
AllowNoIcons=yes
; Uncomment the following line to run in non administrative install mode (install for current user only.)
;PrivilegesRequired=lowest
OutputDir=Output
OutputBaseFilename=NSP_Cosmetic_POS_Setup
SetupIconFile=static\images\favicon.ico
Compression=lzma
SolidCompression=yes
WizardStyle=modern

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "dist\NSP_Cosmetic_POS\NSP_Cosmetic_POS.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "dist\NSP_Cosmetic_POS\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
; NOTE: Don't use "Flags: ignoreversion" on any shared system files

[Icons]
Name: "{group}\NSP Cosmetic POS"; Filename: "{app}\NSP_Cosmetic_POS.exe"
Name: "{autodesktop}\NSP Cosmetic POS"; Filename: "{app}\NSP_Cosmetic_POS.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\NSP_Cosmetic_POS.exe"; Description: "{cm:LaunchProgram,NSP Cosmetic POS}"; Flags: nowait postinstall skipifsilent
