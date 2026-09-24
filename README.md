# Jídelna.cz → Home Assistant

[English version](README.en.md)

Home Assistant integrace, která ze služby [jidelna.cz](https://www.jidelna.cz) stahuje
objednané obědy a zobrazuje je jako **kalendářové entity**. Pro každého strávníka
navázaného na účet lze zvlášť nastavit, jestli se synchronizuje, do jakého kalendáře,
jak vypadá obsah události a jak se počítá čas/délka oběda (celodenní, fixní délka,
nebo rozvrh po dnech s rozlišením sudého/lichého týdne).

Integrace používá neoficiální REST API jidelna.cz — viz [`api/jidelna-cz-api.md`](api/jidelna-cz-api.md)
pro technický popis a poznámky k jeho chování.

## Instalace

### HACS

1. HACS → Integrace → tři tečky vpravo nahoře → Vlastní repozitáře.
2. Přidat `https://github.com/hondzik/jidelnacz-calendar` jako typ „Integrace".
3. Nainstalovat „Jídelna.cz" a restartovat Home Assistant.

### Ručně

Zkopírovat `custom_components/jidelna` do `<config>/custom_components/jidelna`
a restartovat Home Assistant.

## Nastavení

Nastavení → Zařízení a služby → Přidat integraci → „Jídelna.cz".

1. Zadejte přihlašovací údaje na jidelna.cz (ID strávníka nebo e-mail hlavního účtu).
2. Vyberte, které strávníky navázané na účet chcete synchronizovat, a čas denní aktualizace.
3. Pro každého vybraného strávníka projděte průvodce:
   - **Kalendářová entita** — založit novou, nebo přiřadit k už existující (např. sdílený
     kalendář pro sourozence).
   - **Obsah události** — volitelný textový prefix názvu jídla a místo.
   - **Délka oběda** — celodenní událost / fixní délka pro všechny dny / každý den jiná.
   - **Čas oběda** (pokud není celodenní) — volitelně rozlišení sudého a lichého týdne,
     pro každý všední den čas „od" (a u „každý den jiná" i čas „do" — u fixní délky se
     dopočítá automaticky).

Pozdější změny (zapnutí/vypnutí strávníka, úprava rozvrhu, čas aktualizace, ruční
„Aktualizovat teď") jsou v nastavení integrace (options flow).

## Poznámky

- Integrace je jen pro čtení — neobjednává ani neodhlašuje obědy.
- Přihlašovací session u jidelna.cz vydrží ~24 hodin; integrace se v případě potřeby
  sama znovu přihlásí.
- Historie odeslaných událostí se drží až 1 rok zpátky, i mimo aktuálně stahovaný rozsah.
