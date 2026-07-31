[Setup]
AppName=Controle de Horas
AppVersion=1.0.2
AppPublisher=Pabloluan981
DefaultDirName={autopf}\ControleHoras
DefaultGroupName=Controle de Horas
OutputDir=installer
OutputBaseFilename=ControleHoras_Setup
Compression=lzma
SolidCompression=yes
WizardStyle=modern

[Languages]
Name: "brazilianportuguese"; MessagesFile: "compiler:Languages\BrazilianPortuguese.isl"

[Tasks]
Name: "desktopicon"; Description: "Criar atalho na área de trabalho"; GroupDescription: "Ícones adicionais:"

[Files]
Source: "dist\ControleHoras.exe"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\Controle de Horas"; Filename: "{app}\ControleHoras.exe"
Name: "{commondesktop}\Controle de Horas"; Filename: "{app}\ControleHoras.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\ControleHoras.exe"; Description: "Abrir Controle de Horas"; Flags: nowait postinstall skipifsilent