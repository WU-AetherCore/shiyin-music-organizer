Unicode True
!include "MUI2.nsh"
!include "FileFunc.nsh"

!define APP_NAME "Shiyin Music Organizer"
!define APP_VERSION "1.3.41"
!define APP_EXE "ShiyinMusicOrganizer.exe"
!define SOURCE_DIR "E:\WUFUDONG\Linux-Project\RaspberryPi5_Project\build\shiyin-dist\ShiyinMusicOrganizer"
!define ICON_FILE "E:\WUFUDONG\Linux-Project\RaspberryPi5_Project\build\shiyin-app.ico"
!define OUT_FILE "E:\WUFUDONG\Linux-Project\RaspberryPi5_Project\build\nsis-output\ShiyinMusicOrganizer-Setup-1.3.41.exe"

Name "${APP_NAME}"
OutFile "${OUT_FILE}"
InstallDir "$LOCALAPPDATA\Programs\ShiyinMusicOrganizer"
InstallDirRegKey HKCU "Software\ShiyinMusicOrganizer" "InstallDir"
RequestExecutionLevel user
SetCompressor /SOLID lzma
Unicode True

!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_LANGUAGE "English"

Section "Install"
  SetOutPath "$INSTDIR"
  File /r "${SOURCE_DIR}\*.*"
  WriteUninstaller "$INSTDIR\Uninstall.exe"
  WriteRegStr HKCU "Software\ShiyinMusicOrganizer" "InstallDir" "$INSTDIR"
  CreateDirectory "$SMPROGRAMS\拾音"
  CreateShortcut "$SMPROGRAMS\拾音\拾音.lnk" "$INSTDIR\${APP_EXE}" "" "$INSTDIR\${APP_EXE}" 0
  CreateShortcut "$SMPROGRAMS\拾音\Uninstall.lnk" "$INSTDIR\Uninstall.exe"
  CreateShortcut "$DESKTOP\拾音.lnk" "$INSTDIR\${APP_EXE}" "" "$INSTDIR\${APP_EXE}" 0
  WriteUninstaller "$INSTDIR\Uninstall.exe"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\ShiyinMusicOrganizer" "DisplayName" "${APP_NAME}"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\ShiyinMusicOrganizer" "DisplayVersion" "${APP_VERSION}"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\ShiyinMusicOrganizer" "UninstallString" "$INSTDIR\Uninstall.exe"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\ShiyinMusicOrganizer" "DisplayIcon" "$INSTDIR\${APP_EXE}"
SectionEnd

Section "Uninstall"
  Delete "$DESKTOP\拾音.lnk"
  Delete "$SMPROGRAMS\拾音\拾音.lnk"
  Delete "$SMPROGRAMS\拾音\Uninstall.lnk"
  RMDir "$SMPROGRAMS\拾音"
  RMDir /r "$INSTDIR"
  DeleteRegKey HKCU "Software\ShiyinMusicOrganizer"
  DeleteRegKey HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\ShiyinMusicOrganizer"
SectionEnd

Function .onInstSuccess
  IfSilent +2
  ExecShell "open" "$INSTDIR\${APP_EXE}"
FunctionEnd
