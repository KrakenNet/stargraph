# MITRE ATT&CK Subset (offline fixture)

## Initial Access

### T1190 Exploit Public-Facing Application
Adversaries may attempt to exploit a weakness in an Internet-facing host or system to gain access. Common applications include Apache, IIS, MySQL, web servers.

### T1133 External Remote Services
Adversaries may leverage external-facing remote services such as VPNs, SSH, RDP. Maps to CWE-287 (improper authentication).

## Execution

### T1059 Command and Scripting Interpreter
Abuse of command/script interpreters (sh, bash, powershell, python) to run code. Maps to CWE-78 (OS command injection).

## Persistence

### T1547 Boot or Logon Autostart Execution
Configure system settings to execute a program during boot. Related: CWE-732 (incorrect permission assignment).
