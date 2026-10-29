!include nsDialogs.nsh
!define ANELF_INSTALLER_DIR "${__FILEDIR__}"

!macro anelfStagePreservation
  InitPluginsDir
  File /oname=$PLUGINSDIR\preservation.cjs "${ANELF_INSTALLER_DIR}\preservation.cjs"
  File /oname=$PLUGINSDIR\managed.cjs "${ANELF_INSTALLER_DIR}\managed.cjs"
!macroend

!macro anelfRunPreservation EXE DATA MODE
  ReadEnvStr $R7 ELECTRON_RUN_AS_NODE
  System::Call 'Kernel32::SetEnvironmentVariable(t "ELECTRON_RUN_AS_NODE", t "1")'
  nsExec::ExecToLog '"${EXE}" "$PLUGINSDIR\preservation.cjs" ${MODE} "${DATA}"'
  Pop $R0
  System::Call 'Kernel32::SetEnvironmentVariable(t "ELECTRON_RUN_AS_NODE", t rR7)'
  ${if} $R0 != 0
    MessageBox MB_OK|MB_ICONSTOP "Project data could not be prepared safely. No application files were removed. See installation details and retry." /SD IDOK
    Abort
  ${endif}
!macroend

; Old uninstallers run before the new app is extracted. Seed their existing
; preservation flags with the new helper, without changing project contents.
!macro customInit
  !insertmacro anelfStagePreservation
  ReadRegStr $R8 HKCU "${INSTALL_REGISTRY_KEY}" InstallLocation
  ${if} $R8 != ""
  ${andIf} ${FileExists} "$R8\${APP_EXECUTABLE_FILENAME}"
    !insertmacro anelfRunPreservation "$R8\${APP_EXECUTABLE_FILENAME}" "$R8\data" "protect"
  ${endif}
  ReadRegStr $R8 HKLM "${INSTALL_REGISTRY_KEY}" InstallLocation
  ${if} $R8 != ""
  ${andIf} ${FileExists} "$R8\${APP_EXECUTABLE_FILENAME}"
    !insertmacro anelfRunPreservation "$R8\${APP_EXECUTABLE_FILENAME}" "$R8\data" "protect"
  ${endif}
!macroend

!ifdef BUILD_UNINSTALLER
  Var anelfDeleteData
  Var anelfDeleteCheckbox
!endif

!macro customUnInit
  StrCpy $anelfDeleteData 0
  ; Silent uninstall also preserves data unless deletion is explicitly requested.
  ${GetParameters} $R0
  ClearErrors
  ${GetOptions} $R0 "--delete-project-data" $R1
  ${IfNot} ${Errors}
    ${IfNot} ${isUpdated}
      StrCpy $anelfDeleteData 1
    ${endif}
  ${endif}
  ClearErrors
!macroend

!macro customUnWelcomePage
  UninstPage custom un.anelfDataPage un.anelfDataPageLeave

Function un.anelfDataPage
  ${if} ${isUpdated}
    Abort
  ${endif}
  nsDialogs::Create 1018
  Pop $R0
  ${if} $R0 == error
    Abort
  ${endif}
  ${if} $LANGUAGE == 2052
  ${orIf} $LANGUAGE == 1028
    !insertmacro MUI_HEADER_TEXT "卸载 AnelfAgent" "默认保留项目，下次安装可以继续使用。"
    ${NSD_CreateLabel} 0 4u 100% 36u "卸载程序后，项目、运行环境和配置将保留。$\r$\n重新安装到同一目录即可接管原项目。"
    Pop $R0
    ${NSD_CreateCheckbox} 0 52u 100% 24u "同时删除 APP 创建的项目、环境与缓存"
    Pop $anelfDeleteCheckbox
    ${NSD_CreateLabel} 0 85u 100% 38u "勾选后，APP 自建项目中的配置与数据也会删除，无法恢复。$\r$\n接入或迁移的已有项目始终保留。"
    Pop $R0
  ${else}
    !insertmacro MUI_HEADER_TEXT "Uninstall AnelfAgent" "Keep your projects for the next installation."
    ${NSD_CreateLabel} 0 4u 100% 36u "Projects, environments and settings are kept by default.$\r$\nReinstall in the same folder to reconnect to them."
    Pop $R0
    ${NSD_CreateCheckbox} 0 52u 100% 24u "Also delete app-created projects, environments and caches"
    Pop $anelfDeleteCheckbox
    ${NSD_CreateLabel} 0 85u 100% 38u "Deletion includes settings and data in app-created projects and cannot be undone.$\r$\nImported or migrated existing projects are always kept."
    Pop $R0
  ${endif}
  ${NSD_SetState} $anelfDeleteCheckbox $anelfDeleteData
  nsDialogs::Show
FunctionEnd

Function un.anelfDataPageLeave
  ${NSD_GetState} $anelfDeleteCheckbox $anelfDeleteData
FunctionEnd
!macroend

!macro customUnInstall
  ClearErrors
  StrCpy $R0 1
  ${if} ${isUpdated}
    ExecWait '"$INSTDIR\${APP_EXECUTABLE_FILENAME}" --prepare-upgrade' $R0
  ${elseIf} $anelfDeleteData == 1
    ExecWait '"$INSTDIR\${APP_EXECUTABLE_FILENAME}" --prepare-uninstall --delete-project-data' $R0
  ${else}
    ExecWait '"$INSTDIR\${APP_EXECUTABLE_FILENAME}" --prepare-uninstall' $R0
  ${endif}
  ${if} ${Errors}
  ${orIf} $R0 != 0
    MessageBox MB_OK|MB_ICONSTOP "AnelfAgent could not finish safely. Application files were preserved. See data\uninstall-error.log and retry." /SD IDOK
    Abort
  ${endif}
!macroend

!macro customRemoveFiles
  SetOutPath $TEMP
  ${IfNot} ${isUpdated}
    ${if} $anelfDeleteData == 1
      !insertmacro anelfStagePreservation
      !insertmacro anelfRunPreservation "$INSTDIR\${APP_EXECUTABLE_FILENAME}" "$INSTDIR\data" "purge"
    ${endif}
  ${endif}
  StrCpy $R3 0
  ${if} ${isUpdated}
    StrCpy $R3 1
  ${elseIf} $anelfDeleteData != 1
    StrCpy $R3 1
  ${endif}
  IfFileExists "$INSTDIR\data\preserved-projects.json" 0 +2
  StrCpy $R3 1
  ${if} $R3 == 1
    ; APP upgrades replace binaries while preserving the complete managed data tree.
    FindFirst $R1 $R2 "$INSTDIR\*.*"
    ${DoWhile} $R2 != ""
      ${if} $R2 != "."
      ${andIf} $R2 != ".."
      ${andIf} $R2 != "data"
        IfFileExists "$INSTDIR\data\preserve-$R2.flag" anelf_keep_folder
        IfFileExists "$INSTDIR\$R2\*.*" 0 +3
        RMDir /r "$INSTDIR\$R2"
        Goto +2
        Delete "$INSTDIR\$R2"
        IfFileExists "$INSTDIR\$R2" 0 +2
        Abort "Unable to replace an application file. The project data was preserved."
      ${endif}
      anelf_keep_folder:
      FindNext $R1 $R2
    ${Loop}
    FindClose $R1
  ${else}
    RMDir /r "$INSTDIR"
    IfFileExists "$INSTDIR\*.*" 0 +2
    Abort "Some application files are still in use. Close them and retry uninstalling."
  ${endif}
!macroend
