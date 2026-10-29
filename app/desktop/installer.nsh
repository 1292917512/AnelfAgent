!include nsDialogs.nsh
!define ANELF_INSTALLER_DIR "${__FILEDIR__}"

!macro anelfText OUT ZH EN
  ${if} $LANGUAGE == 2052
  ${orIf} $LANGUAGE == 1028
    StrCpy ${OUT} "${ZH}"
  ${else}
    StrCpy ${OUT} "${EN}"
  ${endif}
!macroend

; Check the desktop executable by its exact name, never all processes beneath
; INSTDIR (the installer itself and project Python may live under data/).
; Do not kill a process: the app's own exit flow coordinates service shutdown.
!macro customCheckAppRunning
  ${Do}
    nsProcess::_FindProcess /NOUNLOAD "${APP_EXECUTABLE_FILENAME}"
    Pop $R0
    ${if} $R0 == 603
      ${ExitDo}
    ${endif}
    ${if} $R0 == 0
      !insertmacro anelfText $R9 "AnelfAgent 客户端仍在运行（可能已收至托盘）。请从托盘选择退出后重试；关闭窗口仅收起到托盘。项目数据会保留。" "An AnelfAgent desktop client is still running, possibly in the tray. Choose Quit from its tray menu, then retry. Closing its window only hides it. Project data is preserved."
    ${else}
      !insertmacro anelfText $R9 "无法确认客户端是否已退出。请检查访问权限后重试。项目数据未改动。" "Could not verify that the desktop client exited. Check access permissions and retry. Project data was not changed."
    ${endif}
    MessageBox MB_RETRYCANCEL|MB_ICONEXCLAMATION "$R9" /SD IDCANCEL IDRETRY +3
    SetErrorLevel 2
    Quit
  ${Loop}
!macroend

; Called by the pinned electron-builder template hook. Use the newly built
; cleanup component directly; never execute a broken historical uninstaller.
; Registry lookup, installation scope and --updated remain builder-managed.
!macro customUninstallPrevious
  InitPluginsDir
  StrCpy $uninstallerFileNameTemp "$PLUGINSDIR\anelf-upgrade-uninstaller.exe"
  File /oname=$PLUGINSDIR\anelf-upgrade-uninstaller.exe "${UNINSTALLER_OUT_FILE}"
  ${Do}
    ClearErrors
    ExecWait '"$uninstallerFileNameTemp" /S /KEEP_APP_DATA $0 _?=$installationDir' $R0
    ${if} ${Errors}
      StrCpy $R0 1
    ${endif}
    ${if} $R0 == 0
      ${ExitDo}
    ${endif}
    !insertmacro anelfText $R9 "覆盖安装未能完成。请查看 $installationDir\data\installer-error.log；项目数据已保留。解决后可重试，或取消安装。" "The upgrade could not finish. See $installationDir\data\installer-error.log; project data was preserved. Resolve the cause and retry, or cancel setup."
    MessageBox MB_RETRYCANCEL|MB_ICONEXCLAMATION "$R9" /SD IDCANCEL IDRETRY +3
    SetErrorLevel 2
    Quit
  ${Loop}
!macroend

!macro anelfMaintenanceFailed
  FileOpen $R4 "$INSTDIR\data\installer-error.log" w
  FileWrite $R4 "$R9$\r$\n"
  FileClose $R4
  Abort "$R9"
!macroend

!macro anelfStagePreservation
  InitPluginsDir
  File /oname=$PLUGINSDIR\preservation.cjs "${ANELF_INSTALLER_DIR}\preservation.cjs"
  File /oname=$PLUGINSDIR\managed.cjs "${ANELF_INSTALLER_DIR}\managed.cjs"
!macroend

!macro anelfRunPreservation EXE DATA MODE
  System::Call 'Kernel32::SetEnvironmentVariable(t "ELECTRON_RUN_AS_NODE", t "1")'
  nsExec::ExecToLog '"${EXE}" "$PLUGINSDIR\preservation.cjs" ${MODE} "${DATA}"'
  Pop $R0
  ; Electron treats any present value, including "0" and "", as Node mode.
  ; Delete the process-local variable before launching desktop maintenance.
  System::Call 'Kernel32::SetEnvironmentVariable(t "ELECTRON_RUN_AS_NODE", p 0)'
  ${if} $R0 != 0
    !insertmacro anelfText $R9 "未能完成项目数据保护。程序与项目均未删除，请展开安装详情后重试。" "Project data could not be protected. No application or project files were removed. Check installation details and retry."
    MessageBox MB_OK|MB_ICONSTOP "$R9" /SD IDOK
    Abort
  ${endif}
!macroend

; Old uninstallers run before the new app is extracted. Seed their existing
; preservation flags with the new helper, without changing project contents.
!macro customInit
  System::Call 'Kernel32::SetEnvironmentVariable(t "ELECTRON_RUN_AS_NODE", p 0)'
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

!macro customInstall
  Delete "$INSTDIR\data\installer-prepared.flag"
  Delete "$INSTDIR\data\installer-error.log"
!macroend

!ifdef BUILD_UNINSTALLER
  Var anelfDeleteData
  Var anelfDeleteCheckbox
!endif

!macro customUnInit
  System::Call 'Kernel32::SetEnvironmentVariable(t "ELECTRON_RUN_AS_NODE", p 0)'
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
  System::Call 'Kernel32::SetEnvironmentVariable(t "ELECTRON_RUN_AS_NODE", p 0)'
  ; A prior attempt may already have removed the old executable. Resume only
  ; after this exact directory completed maintenance successfully.
  ${if} ${isUpdated}
  ${andIfNot} ${FileExists} "$INSTDIR\${APP_EXECUTABLE_FILENAME}"
    ClearErrors
    StrCpy $R8 ""
    FileOpen $R4 "$INSTDIR\data\installer-prepared.flag" r
    FileRead $R4 $R8
    FileClose $R4
    ${if} $R8 == $INSTDIR
      Goto anelf_maintenance_complete
    ${endif}
  ${endif}
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
    !insertmacro anelfText $R9 "未能完成安全维护，程序和项目已保留。请查看 data\uninstall-error.log，解决后重试。" "Safe maintenance did not finish. Application and project files were preserved. Check data\uninstall-error.log and retry."
    !insertmacro anelfMaintenanceFailed
  ${endif}
  ; Electron's helper processes can outlive the maintenance main process.
  ; Wait for their image handles before replacing any application files.
  StrCpy $R6 0
  ${Do}
    nsProcess::_FindProcess /NOUNLOAD "${APP_EXECUTABLE_FILENAME}"
    Pop $R0
    ${if} $R0 == 603
      ${ExitDo}
    ${endif}
    IntOp $R6 $R6 + 1
    ${if} $R6 >= 100
      !insertmacro anelfText $R9 "维护进程尚未完全退出，程序文件暂未替换。请稍后重试，项目数据已保留。" "Maintenance processes have not fully exited. Application files were not replaced. Retry shortly; project data was preserved."
      !insertmacro anelfMaintenanceFailed
    ${endif}
    Sleep 100
  ${Loop}
  ${if} ${isUpdated}
    ClearErrors
    FileOpen $R4 "$INSTDIR\data\installer-prepared.flag" w
    FileWrite $R4 "$INSTDIR"
    FileClose $R4
    ${if} ${Errors}
      !insertmacro anelfText $R9 "无法保存升级恢复记录，程序文件尚未删除。请检查数据目录权限与剩余空间后重试。" "Could not save the upgrade recovery record. Application files were not removed. Check data-folder permissions and disk space, then retry."
      !insertmacro anelfMaintenanceFailed
    ${endif}
  ${endif}
  anelf_maintenance_complete:
  Delete "$INSTDIR\data\installer-error.log"
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
        StrCpy $R6 0
        ${Do}
          ClearErrors
          IfFileExists "$INSTDIR\$R2\*.*" 0 +3
          RMDir /r "$INSTDIR\$R2"
          Goto +2
          Delete "$INSTDIR\$R2"
          ${IfNot} ${FileExists} "$INSTDIR\$R2"
            ${ExitDo}
          ${endif}
          IntOp $R6 $R6 + 1
          ${if} $R6 >= 30
            !insertmacro anelfText $R9 "无法替换程序文件：$INSTDIR\$R2。项目数据已保留，请关闭占用程序或检查权限后重试。" "Cannot replace application file: $INSTDIR\$R2. Project data was preserved. Close programs using it or check permissions, then retry."
            !insertmacro anelfMaintenanceFailed
          ${endif}
          Sleep 100
        ${Loop}
      ${endif}
      anelf_keep_folder:
      FindNext $R1 $R2
    ${Loop}
    FindClose $R1
  ${else}
    RMDir /r "$INSTDIR"
    ${if} ${FileExists} "$INSTDIR\*.*"
      !insertmacro anelfText $R9 "部分程序文件仍被占用。请关闭相关程序后重试卸载。" "Some application files are still in use. Close the programs using them and retry uninstalling."
      Abort "$R9"
    ${endif}
  ${endif}
!macroend
