"""Apply the unified account UI without replacing unrelated production code."""
from pathlib import Path
import re
import sys

def once(s, old, new):
    if s.count(old) != 1:
        raise RuntimeError("Unexpected integration anchor: " + old[:120])
    return s.replace(old, new, 1)

def patch(root):
    folder = root / "app/static"
    target = folder / "orion-suite.js"
    s = target.read_text()
    fragment = (folder / "orion-account-unified.fragment.js").read_text()
    if "const accountSections=[" in s:
        return
    s = once(s, "  function renderSidebar(){", fragment+"\n  function renderSidebar(){")
    s = once(s, "    const structured=barbershop||dental||subscription||affiliate;",
             "    const structured=barbershop||dental||subscription||affiliate;\n    $('appView')?.classList.toggle('os-personal-account',personalAccount());")
    # Remove the old independent sidebar entry (only member workspaces).
    pattern = r"    if\(\(subscription\|\|affiliate\)&&routePermitted\('configuracoes'\)\)nav\+=.*data-os-app-settings=\"appearance\".*;\n"
    s, count = re.subn(pattern, "", s)
    if count != 1:
        raise RuntimeError("Appearance sidebar anchor changed")
    # Preserve admin/support behavior; member profile now has one entry for account.
    s = once(s, "['Configurações e conta',[['configuracoes','settings'],['novidades','bell'],['ajuda','book'],['member','user']]]",
             "['Ajuda e conta',[['novidades','bell'],['ajuda','book'],['member','user']]]")
    s = once(s, "const appearance=(['Site e configurações','Configurações e conta'].includes(heading)||isAffiliate&&heading==='Configuração')&&settingsTabAllowed('appearance')?",
             "const appearance=!personalAccount()&&(['Site e configurações','Configurações e conta'].includes(heading)||isAffiliate&&heading==='Configuração')&&settingsTabAllowed('appearance')?")
    # Extract the original appearance cards, rather than recreate any preferences.
    start = s.index("    if(tab==='appearance')panel=")
    end = s.index("    if(tab==='ai'){", start)
    block = s[start:end]
    if "appShortcutEditor()" not in block or "os-appearance-options" not in block:
        raise RuntimeError("Appearance card anchor changed")
    s = s[:start]+"    if(tab==='appearance')panel=appearancePanel();\n"+s[end:]
    s = once(s, "  function renderSettings(){",
             "  function appearancePanel(){\n    const tab='appearance';let panel='';\n"+block+"    return panel;\n  }\n  function renderSettings(){")
    # Legacy member route must dispose of affiliate state and mount account sections.
    old = "function legacyOpened(route){stopHomeLive();"
    s = once(s, old, "function legacyOpened(route){disposeAffiliate();stopHomeLive();")
    old = "drawer(false);document.title=routeLabel(route)+' · Orion';}"
    s = once(s, old, "drawer(false);document.title=routeLabel(route)+' · Orion';if(route==='member')renderMemberAccount();}")
    # Old settings links for appearance/account remain compatible without exposing duplicates.
    s = once(s, "    if(!routePermitted(route))route=fallbackRoute();",
             "    if(route==='configuracoes'&&personalAccount()&&(personalSettingsTab(state.filters.settingsTab)||!businessSettingsAvailable())){\n      state.filters.accountTab=state.filters.settingsTab==='account'?'profile':'appearance';\n      state.filters.settingsTab='general';route='member';\n    }\n    if(!routePermitted(route))route=fallbackRoute();")
    s = once(s, "    if(!settingsTabAllowed(state.filters.settingsTab))state.filters.settingsTab='account';",
             "    if(!settingsTabAllowed(state.filters.settingsTab))state.filters.settingsTab=personalAccount()?(settingsSections.find(([key])=>!personalSettingsTab(key)&&settingsTabAllowed(key))?.[0]||'general'):'account';")
    s = once(s, "||key==='whatsapp'&&permitted('conversations.manual')),\n      tab=sections",
             "||key==='whatsapp'&&permitted('conversations.manual')).filter(([key])=>!personalAccount()||!personalSettingsTab(key)),\n      tab=sections")
    s = once(s, "    const t=tenant(),isAdmin=['support','admin'].includes(state.bridge.user?.role),\n      sections=settingsSections",
             "    if(personalAccount()&&!businessSettingsAvailable()){accountSection('profile');return;}\n    const t=tenant(),isAdmin=['support','admin'].includes(state.bridge.user?.role),\n      sections=settingsSections")
    s = once(s, "      tab=sections.some(item=>item[0]===state.filters.settingsTab)?state.filters.settingsTab:'general',",
             "      tab=sections.some(item=>item[0]===state.filters.settingsTab)?state.filters.settingsTab:sections[0]?.[0]||'general',")
    # State scoped to session; default tab reset through defaultFilters on sign-out.
    s = once(s, "settingsTab:'general',appHoursView:", "settingsTab:'general',accountTab:'profile',appHoursView:")
    # Reuse existing layout / shortcuts state and event handling from appearance.
    s = s.replace("if(state.route==='configuracoes'&&state.filters.settingsTab==='appearance'){", "if(appearanceView()){")
    s = s.replace("if(!appLayoutEligible()||state.route!=='configuracoes'||state.filters.settingsTab!=='appearance')return;",
                  "if(!appLayoutEligible()||!appearanceView())return;")
    s = once(s, "renderSettings();$('osAppShortcut'+index)?.focus({preventScroll:true});",
             "refreshAppearanceView();$('osAppShortcut'+index)?.focus({preventScroll:true});")
    s = once(s, "renderAppDock();renderSettings();\n    $('osPageBody')?.querySelector('[data-os-action=app-shortcuts-save]')",
             "renderAppDock();refreshAppearanceView();\n    appearanceRoot()?.querySelector('[data-os-action=app-shortcuts-save]')")
    s = once(s, "      renderSettings();\n      $('osPageBody')?.querySelector('[data-os-layout=",
             "      refreshAppearanceView();\n      appearanceRoot()?.querySelector('[data-os-layout=")
    s = once(s, "renderSettings();$('osAppShortcut0')?.focus({preventScroll:true});return;",
             "refreshAppearanceView();$('osAppShortcut0')?.focus({preventScroll:true});return;")
    s = once(s, "if(appLayoutEligible()&&state.route==='configuracoes'&&state.filters.settingsTab==='appearance')changeAppShortcut",
             "if(appLayoutEligible()&&appearanceView())changeAppShortcut")
    s = once(s, "      if(el.dataset.osLayout){chooseAppLayout(el.dataset.osLayout);return;}",
             "      if(el.dataset.osAccountTab){accountSection(el.dataset.osAccountTab);return;}\n      if(el.dataset.osLayout){chooseAppLayout(el.dataset.osLayout);return;}")
    s = once(s, "if(el.dataset.osAppSettings){if(!(appLayoutEnabled()||subscriptionWorkspace()||affiliateWorkspace())",
             "if(el.dataset.osAppSettings){if(personalAccount()&&personalSettingsTab(el.dataset.osAppSettings)){accountSection(el.dataset.osAppSettings==='appearance'?'appearance':'profile');return;}if(!(appLayoutEnabled()||subscriptionWorkspace()||affiliateWorkspace())")
    s = once(s, "if(el.dataset.osSettings){state.filters.settingsTab=el.dataset.osSettings;",
             "if(el.dataset.osSettings){if(personalAccount()&&personalSettingsTab(el.dataset.osSettings)){accountSection(el.dataset.osSettings==='appearance'?'appearance':'profile');return;}state.filters.settingsTab=el.dataset.osSettings;")
    s = once(s, "if(el.id==='osSettingsSection'){state.filters.settingsTab=el.value;",
             "if(el.id==='osSettingsSection'){if(personalAccount()&&personalSettingsTab(el.value)){accountSection(el.value==='appearance'?'appearance':'profile');return;}state.filters.settingsTab=el.value;")
    # The help article follows the new route instead of a retired standalone page.
    s = s.replace("'Como personalizar o painel de Afiliados','Escolha o tema, o layout e os atalhos desta conta.','configuracoes'",
                  "'Como personalizar o painel de Afiliados','Escolha o tema, o layout e os atalhos desta conta.','member'")
    s = s.replace("'Abra Aparência para escolher Claro, Escuro ou Sistema.", "'Abra Minha conta > Aparência para escolher Claro, Escuro ou Sistema.")
    target.write_text(s)
    style = folder / "orion-suite.css"
    addition = (folder / "orion-account-unified.css").read_text()
    style.write_text(style.read_text().rstrip()+"\n\n"+addition)

if __name__ == "__main__":
    patch(Path(sys.argv[1] if len(sys.argv)>1 else "."))
