; 墨软·工作台 NSIS 安装脚本（onedir 包 → 安装程序）
; 编译：makensis /DAPP_VERSION=1.0.2 packaging\installer.nsi
; 路径默认相对本脚本（仓库内），可用 /DSOURCE_DIR=... /DOUTPUT_DIR=... 覆盖
Unicode true
!include "MUI2.nsh"

!ifndef APP_VERSION
  !define APP_VERSION "1.0.2"   ; 仅手动编译时的兜底；正式打包由 build_installer.ps1 传 /DAPP_VERSION
!endif
!ifndef SOURCE_DIR
  !define SOURCE_DIR "${__FILEDIR__}\..\dist\ModuWorkbench"
!endif
!ifndef OUTPUT_DIR
  !define OUTPUT_DIR "${__FILEDIR__}\..\dist"
!endif
!ifndef ASSETS_DIR
  !define ASSETS_DIR "${__FILEDIR__}\..\src\modu_workbench\assets"
!endif

; 品牌图形（由 packaging/make_icons.py 生成）
!define MUI_ICON "${ASSETS_DIR}\app.ico"
!define MUI_UNICON "${ASSETS_DIR}\app.ico"
!define MUI_HEADERIMAGE
!define MUI_HEADERIMAGE_BITMAP "${ASSETS_DIR}\installer_header.bmp"
!define MUI_HEADERIMAGE_RIGHT
!define MUI_WELCOMEFINISHPAGE_BITMAP "${ASSETS_DIR}\installer_welcome.bmp"
!define MUI_UNWELCOMEFINISHPAGE_BITMAP "${ASSETS_DIR}\installer_welcome.bmp"

Name "墨软·工作台"
OutFile "${OUTPUT_DIR}\墨软工作台-Setup-${APP_VERSION}.exe"
InstallDir "$LOCALAPPDATA\Programs\墨软工作台"
InstallDirRegKey HKCU "Software\墨软工作台" "InstallLocation"
RequestExecutionLevel user
SetCompressor zlib
BrandingText "墨软·工作台"

!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_LANGUAGE "SimpChinese"

Section "墨软·工作台" SEC_MAIN
  SetOutPath "$INSTDIR"
  File /r "${SOURCE_DIR}\*.*"
  WriteUninstaller "$INSTDIR\Uninstall.exe"
  CreateDirectory "$SMPROGRAMS\墨软工作台"
  CreateShortcut "$SMPROGRAMS\墨软工作台\墨软·工作台.lnk" "$INSTDIR\ModuWorkbench.exe"
  CreateShortcut "$SMPROGRAMS\墨软工作台\卸载墨软工作台.lnk" "$INSTDIR\Uninstall.exe"
  CreateShortcut "$DESKTOP\墨软·工作台.lnk" "$INSTDIR\ModuWorkbench.exe"
  WriteRegStr HKCU "Software\墨软工作台" "InstallLocation" "$INSTDIR"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\墨软工作台" "DisplayName" "墨软·工作台"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\墨软工作台" "DisplayVersion" "${APP_VERSION}"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\墨软工作台" "Publisher" "ModuWorkbench"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\墨软工作台" "UninstallString" "$\"$INSTDIR\Uninstall.exe$\""
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\墨软工作台" "InstallLocation" "$INSTDIR"
SectionEnd

Section "Uninstall"
  Delete "$DESKTOP\墨软·工作台.lnk"
  RMDir /r "$SMPROGRAMS\墨软工作台"
  DeleteRegKey HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\墨软工作台"
  DeleteRegKey HKCU "Software\墨软工作台"
  RMDir /r "$INSTDIR"
SectionEnd
