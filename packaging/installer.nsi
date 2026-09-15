; 墨读·工作台 NSIS 安装脚本（onedir 包 → 安装程序）
; 编译：makensis /DAPP_VERSION=0.3.0 packaging\installer.nsi
; 路径默认相对本脚本（仓库内），可用 /DSOURCE_DIR=... /DOUTPUT_DIR=... 覆盖
Unicode true
!include "MUI2.nsh"

!ifndef APP_VERSION
  !define APP_VERSION "0.3.0"
!endif
!ifndef SOURCE_DIR
  !define SOURCE_DIR "${__FILEDIR__}\..\dist\ModuWorkbench"
!endif
!ifndef OUTPUT_DIR
  !define OUTPUT_DIR "${__FILEDIR__}\..\dist"
!endif

Name "墨读·工作台"
OutFile "${OUTPUT_DIR}\墨读工作台-Setup-${APP_VERSION}.exe"
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
  File /r "${SOURCE_DIR}\*.*"
  WriteUninstaller "$INSTDIR\Uninstall.exe"
  CreateDirectory "$SMPROGRAMS\墨读工作台"
  CreateShortcut "$SMPROGRAMS\墨读工作台\墨读·工作台.lnk" "$INSTDIR\ModuWorkbench.exe"
  CreateShortcut "$SMPROGRAMS\墨读工作台\卸载墨读工作台.lnk" "$INSTDIR\Uninstall.exe"
  CreateShortcut "$DESKTOP\墨读·工作台.lnk" "$INSTDIR\ModuWorkbench.exe"
  WriteRegStr HKCU "Software\墨读工作台" "InstallLocation" "$INSTDIR"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\墨读工作台" "DisplayName" "墨读·工作台"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\墨读工作台" "DisplayVersion" "${APP_VERSION}"
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
