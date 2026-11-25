; Необязательный установщик Inno Setup.
; Сначала запустите BUILD_EXE.bat, затем откройте этот файл в Inno Setup Compiler.

[Setup]
AppId={{C6DBA092-A00B-4E45-9C02-67B8D543A2A1}
AppName=Моя ИИшка
AppVersion=4.1.1
AppPublisher=Моя ИИшка
DefaultDirName={autopf}\Моя ИИшка
DefaultGroupName=Моя ИИшка
OutputDir=installer_output
OutputBaseFilename=MoyaIshka_Setup
SetupIconFile=app_icon.ico
UninstallDisplayIcon={app}\MoyaIshka.exe
PrivilegesRequired=lowest
ArchitecturesInstallIn64BitMode=x64
Compression=lzma
SolidCompression=yes
WizardStyle=modern

[Languages]
Name: "russian"; MessagesFile: "compiler:Languages\Russian.isl"

[Tasks]
Name: "desktopicon"; Description: "Создать значок на рабочем столе"; GroupDescription: "Дополнительные значки:"; Flags: unchecked

[Files]
Source: "dist\MoyaIshka.exe"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\Моя ИИшка"; Filename: "{app}\MoyaIshka.exe"
Name: "{autodesktop}\Моя ИИшка"; Filename: "{app}\MoyaIshka.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\MoyaIshka.exe"; Description: "Запустить Мою ИИшку"; Flags: postinstall nowait skipifsilent
