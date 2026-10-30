def render_checklist(receipt, drive, manifest, *, payload_path=None, backup_path=None):
    """Plain-text list of the steps that can only happen on the console. receipt may be None (dry run)."""
    versions = []
    if receipt:
        for cid, entry in receipt["components"].items():
            versions.append(f"  - {manifest.component(cid).name}: {entry['tag']}")
    lines = [
        "SWITCH SETUP CHECKLIST",
        "=" * 22,
        "",
        f"Card: {drive.letter}: ({drive.label or 'no label'})",
    ]
    if versions:
        lines += ["Installed:", *versions]
    if backup_path:
        lines.append(f"Card backup on this PC: {backup_path}")
    lines += [
        "",
        "Do these on the console, in order. The tool cannot do them for you.",
        "",
        "1. Eject the card safely in Windows, put it in the console, power the console off.",
        "",
        "2. Boot hekate over USB (RCM).",
        "   - Put the console in RCM (jig or shorting tool on the right rail, hold Vol+, press Power).",
        "   - Connect it to the PC and send the hekate payload with the injector you normally use.",
        f"   - Payload file: {payload_path or 'payload.bin at the root of the card'}",
        "",
        "3. Back up the console's own NAND before anything else (hekate > Tools > Backup eMMC).",
        "   - Back up BOOT0/BOOT1 and eMMC RAW GPP. RAW GPP is the big one (about the size of the console's storage).",
        "   - Copy the card's backup folder to the PC, check RAW GPP is there, then delete it from the card.",
        "",
        "4. Dump your own keys (prod.keys) so you can decrypt your own dumps later.",
        "   - Lockpick_RCM is not downloaded by this tool (see the manual step it printed).",
        "",
        "5. Create the emuMMC (hekate > emuMMC > Create emuMMC).",
        "   - 'SD File' is the simplest. A raw partition needs hekate's partition tool, which erases the card.",
        "   - Boot 'Atmosphere emuMMC'. System Settings > System should end in 'AMS x.y.z|E'.",
        "",
        "6. Keep the emuMMC offline so the console cannot talk to Nintendo from it.",
        "   - Turn Airplane Mode on.",
        "   - The card already has atmosphere/hosts/emummc.txt blocking Nintendo servers (DNS.MITM).",
        "   - Optional second layer: set DNS manually in the Wi-Fi settings to 90DNS.",
        "       Europe/Asia: 163.172.141.219 then 207.246.121.77.",
        "       Americas:    207.246.121.77 then 163.172.141.219.",
        "   - Run the 90DNS Tester app from the homebrew menu; every nintendo domain should show as blocked.",
        "   - The emuMMC and sysNAND keep separate Wi-Fi settings.",
        "",
        "7. Games.",
        "   - Install only dumps of games you own (nxdumptool from your own cartridges or downloads), through Sphaira or Goldleaf.",
        "   - An NSP with a modified ticket is rejected with error 0x291. This tool does not work around that.",
        "",
        "8. Before you update the console's firmware or recreate the emuMMC:",
        "   - Check that this Atmosphere release supports that firmware. Run `sdsetup verify` after any change.",
        "",
    ]
    return "\n".join(lines)
