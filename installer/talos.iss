; Talos Studio 安装脚本（Inno Setup 6）
#define MyAppName "Talos Studio"
#define MyAppVersion "1.0.0"
#define MyAppPublisher "Talos"
#define MyAppExeName "启动.bat"

[Setup]
AppId={{8E4C1A52-6B7D-4C1E-9A3F-1B7C9D0E2F31}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={localappdata}\TalosStudio
DisableDirPage=no
UsePreviousAppDir=no
UsePreviousGroup=no
DisableProgramGroupPage=yes
OutputDir=..\dist
OutputBaseFilename=Talos-Studio-Setup
SetupIconFile=..\static\character.ico
UninstallDisplayIcon={app}\static\character.ico
UninstallDisplayName={#MyAppName}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=lowest
MinVersion=10.0

[Languages]
Name: "en"; MessagesFile: "compiler:Default.isl"

[Messages]
SetupWindowTitle=%1 安装程序
WelcomeLabel1=欢迎使用 [name]
WelcomeLabel2=这会把 {#MyAppName} 安装到你的电脑上，自带运行环境，不需要装 Python，也不需要联网。
SelectDirLabel3=安装程序将把 [name] 安装到下面的文件夹。
SelectDirBrowseLabel=点“下一步”继续；想换位置点“浏览”。
ReadyLabel1=准备就绪，可以开始安装。
ReadyLabel2a=点“安装”开始，点“上一步”还能改。
InstallingLabel=正在安装，请稍候…
FinishedHeadingLabel=[name] 安装完成
FinishedLabelNoIcons=安装完成。
FinishedLabel=安装完成。桌面上已经有快捷方式。
ClickFinish=点“完成”关闭安装程序。
RunEntryExec=启动 {#MyAppName}

[Messages]
SetupWindowTitle=%1
SetupAppTitle=安装程序
WelcomeLabel1=欢迎使用 [name]
WelcomeLabel2=这会把 {#MyAppName} 安装到你的电脑上。自带运行环境，不需要装 Python，也不需要联网。
SelectDirLabel3=安装程序将把 [name] 安装到下面的文件夹。
SelectDirBrowseLabel=点“下一步”继续；想换位置点“浏览”。
SelectDirDesc=选择安装位置
DiskSpaceMBLabel=至少需要 [mb] MB 磁盘空间。
ReadyLabel1=准备就绪，可以开始安装。
ReadyLabel2a=点“安装”开始。安装过程中不要关闭窗口。
InstallingLabel=正在安装，请稍候…
FinishedHeadingLabel=[name] 安装完成
FinishedLabel=安装完成，桌面上已经有快捷方式了。
FinishedLabelNoIcons=安装完成。
ClickFinish=点“完成”关闭安装程序。
ButtonBack=< 上一步
ButtonNext=下一步 >
ButtonInstall=安装
ButtonCancel=取消
ButtonFinish=完成
ButtonBrowse=浏览…
ButtonYes=是
ButtonNo=否
ExitSetupTitle=退出安装
ExitSetupMessage=还没装完，确定要退出吗？
ConfirmUninstall=确定要卸载 %1 吗？
UninstalledAll=%1 已卸载完成。

[Files]
Source: "..\build\installer-src\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"; IconFilename: "{app}\static\character.ico"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"; IconFilename: "{app}\static\character.ico"

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "启动 {#MyAppName}"; Flags: postinstall shellexec skipifsilent nowait
