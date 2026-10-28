!macro customUnInstall
  ${if} ${isUpdated}
    ExecWait '"$INSTDIR\${APP_EXECUTABLE_FILENAME}" --prepare-upgrade' $R0
  ${else}
    ExecWait '"$INSTDIR\${APP_EXECUTABLE_FILENAME}" --prepare-uninstall' $R0
  ${endif}
  ${if} $R0 != 0
    MessageBox MB_OK|MB_ICONSTOP "AnelfAgent could not stop safely. No project data was removed. Close active operations and try again."
    Abort
  ${endif}
!macroend

!macro customRemoveFiles
  SetOutPath $TEMP
  ${if} ${isUpdated}
    ; APP upgrades replace binaries while preserving the complete managed data tree.
    FindFirst $R1 $R2 "$INSTDIR\*.*"
    ${DoWhile} $R2 != ""
      ${if} $R2 != "."
      ${andIf} $R2 != ".."
      ${andIf} $R2 != "data"
        IfFileExists "$INSTDIR\$R2\*.*" 0 +3
        RMDir /r "$INSTDIR\$R2"
        Goto +2
        Delete "$INSTDIR\$R2"
        IfFileExists "$INSTDIR\$R2" 0 +2
        Abort "Unable to replace an application file. The project data was preserved."
      ${endif}
      FindNext $R1 $R2
    ${Loop}
    FindClose $R1
  ${else}
    RMDir /r "$INSTDIR"
    IfFileExists "$INSTDIR\*.*" 0 +2
    Abort "Some application files are still in use. Close them and retry uninstalling."
  ${endif}
!macroend
