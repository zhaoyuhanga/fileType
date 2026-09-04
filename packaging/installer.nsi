; 墨读·工作台 NSIS 安装脚本（onedir 包 → 安装程序）
Unicode true
!include "MUI2.nsh"

Name "墨读·工作台"
OutFile "D:\awork\fileType\dist\墨读工作台-Setup-0.3.0.exe"
InstallDir "$LOCALAPPDATA\Programs\墨读工作台"
InstallDirRegKey HKCU "Software\墨读工作台" "InstallLocation"
RequestExecutionLevel user
SetCompressor zlib
BrandingText "墨读·工作台"

!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_LANGUAGE "SimpChinese"

Section "墨读·工作台" SEC_MAIN
  SetOutPath "$INSTDIR"
  File /r "D:\awork\fileType\dist\ModuWorkbench\*.*"
  WriteUninstaller "$INSTDIR\Uninstall.exe"
  CreateDirectory "$SMPROGRAMS\墨读工作台"
  CreateShortcut "$SMPROGRAMS\墨读工作台\墨读·工作台.lnk" "$INSTDIR\ModuWorkbench.exe"
  CreateShortcut "$SMPROGRAMS\墨读工作台\卸载墨读工作台.lnk" "$INSTDIR\Uninstall.exe"
  CreateShortcut "$DESKTOP\墨读·工作台.lnk" "$INSTDIR\ModuWorkbench.exe"
  WriteRegStr HKCU "Software\墨读工作台" "InstallLocation" "$INSTDIR"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\墨读工作台" "DisplayName" "墨读·工作台"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\墨读工作台" "DisplayVersion" "0.3.0"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\墨读工作台" "Publisher" "ModuWorkbench"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\墨读工作台" "UninstallString" "$\"$INSTDIR\Uninstall.exe$\""
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\墨读工作台" "InstallLocation" "$INSTDIR"
SectionEnd

Section "Uninstall"
  Delete "$DESKTOP\墨读·工作台.lnk"
  RMDir /r "$SMPROGRAMS\墨读工作台"
  DeleteRegKey HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\墨读工作台"
  DeleteRegKey HKCU "Software\墨读工作台"
  RMDir /r "$INSTDIR"
SectionEnd
