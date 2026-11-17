
Sub bat
 echo off & cls
 pushd "%~dp0"
 echo create_shortcut
 start wscript -e:vbs "%~f0"
 popd
 Exit Sub
End Sub

Sub shortcut(name, mainfile)
 Set WshShell = WScript.CreateObject("WScript.Shell")
 strCurrDir = WshShell.currentdirectory
 strDesktop = WshShell.SpecialFolders("Desktop")

 set oShellLink = WshShell.CreateShortcut(strDesktop & "\" & name & ".lnk")
 oShellLink.TargetPath = strCurrDir & "\" & mainfile 
 oShellLink.WindowStyle = 3
 oShellLink.IconLocation = strCurrDir & "\favicon.ico"
 oShellLink.WorkingDirectory = strCurrDir
 oShellLink.Save 
 
 strStartMenu = WshShell.SpecialFolders("AllUsersPrograms")
 set oShellLink = WshShell.CreateShortcut(strStartMenu & "\" & name & ".lnk")
 oShellLink.TargetPath = strCurrDir & "\" & mainfile 
 oShellLink.WindowStyle = 3
 oShellLink.IconLocation = strCurrDir & "\favicon.ico"
 oShellLink.WorkingDirectory = strCurrDir
 oShellLink.Save 
 
End Sub
shortcut "CS·´¿Ö¾«Ó¢", "gamestarter.exe"
