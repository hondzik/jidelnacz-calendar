# Neoficiální REST API jidelna.cz

Rekonstrukce z Go klienta [matejp0/jidelna](https://github.com/matejp0/jidelna) (2022),
**ověřená a doplněná živými odpověďmi serveru ze září 2026**. Není to oficiální
dokumentace. Vše, co je níže označené jako *neověřeno*, je odhad.

Backend běží na Nette 3 (PHP), odpovědi jsou JSON v UTF-8.

## Base URL

```
https://www.jidelna.cz/rest/u/c58zbtfnjz72h6t5nzfva9uzvbag8m
```

Hash v cestě je v klientovi natvrdo od roku 2022 a **v září 2026 pořád funguje**.
Co znamená, nevíme — nejspíš klíč aplikace nebo verze API. Pokud přestane platit,
dá se aktuální odchytit z mobilní appky Jídelna.cz (mitmproxy), volá stejný tvar
`/rest/u/<hash>/`.

Požadavky s tělem posílají `application/x-www-form-urlencoded`, **ne JSON**.

### Obálka odpovědi

Zapisovací operace a login vracejí:

```json
{ "stav": "ok", "tag": "login_success", "zprava": "Uživatel úspěšně přihlášen" }
```

`tag` je strojově čitelný kód, `zprava` text pro uživatele.

**Update 2026-09-24, ověřeno živě:** chybová odpověď u loginu chodí s **HTTP 403**,
NE 200, jak tenhle dokument dřív jen odhadoval. Tělo je ale pořád ta samá JSON
obálka:

```
$ curl -i -X POST .../login/jmenoheslo -d "login=0000000&heslo=spatne"
HTTP/2 403
...
{"stav":"chyba","tag":"invalid_login","zprava":"Nepodařilo se přihlásit uživatele"}
```

Takže **kontroluj `stav` v těle, ne status kód** platí pořád — jen nespoléhej,
že status bude 200. Klient musí JSON parsovat před (případným) `raise_for_status()`,
jinak se HTTP chyba vyhodí dřív, než se stihne přečíst `stav`/`tag`/`zprava`.
Nezachyceno zatím: podoba chyby u zapisovacích operací (objednávka) — možná taky 403,
možná ne, netestováno.

### Hlavičky

`Vary: X-Requested-With` — server rozlišuje AJAX. Kdyby endpoint vracel HTML
místo JSONu, zkus `-H "X-Requested-With: XMLHttpRequest"`.

## Autentizace

### `POST /login/jmenoheslo`

| Pole | Význam |
|---|---|
| `login` | ID strávníka (6–10 číslic) nebo e-mail hlavního účtu |
| `heslo` | heslo |

Odpověď:

```json
{
  "stav": "ok",
  "tag": "login_success",
  "zprava": "Uživatel úspěšně přihlášen",
  "ucet": {
    "typ": "id",
    "login": "2803121",
    "ucty": {
      "3632660": {
        "id": 3632660,
        "regc": "28",
        "login": "2803121",
        "generovaneHeslo": false,
        "vyzvedavaNaWebu": false,
        "maxPocetPorci": 1,
        "blokovatWeb": false,
        "anonymizovaneOU": false,
        "debetDnu": 0,
        "debetKc": 0.0,
        "kontoProObjednavani": "-820"
      }
    }
  }
}
```

**Dvě různá ID, snadno se zamění:**

- `ucet.login` (`"2803121"`) — přihlašovací ID, do dalších volání se **nepoužívá**
- klíč mapy `ucty`, shodný s `id` uvnitř (`3632660`) — to je **`idUzivatele`**
- `regc` (`"28"`) — **`idZarizeni`**, interní ID jídelny

`ucet.typ` je `"id"` u přihlášení ID strávníka. U hlavního účtu přes e-mail
(sdružuje až několik dětí) bude jiný a `ucty` bude mít víc klíčů — *neověřeno*,
ale integrace pro víc strávníků na tom stojí, iteruj mapu, neber první klíč.

`kontoProObjednavani` je už tady, takže pro stav konta **nemusíš volat
`/uzivatel/{id}/info`**.

### Session cookies

Server během loginu regeneruje session a v odpovědi pošle **dvě `PHPSESSID`**
plus `_nss`. Platná je ta **druhá**. Standardní cookie jar (curl `-c`,
`requests.Session()`) přepíše duplicitu poslední hodnotou a udělá správnou věc —
hack `resp.Cookies()[1:]` z Go klienta replikovat netřeba.

Platnost 24 hodin (`Max-Age=86400`). Re-login po expiraci si musíš ošetřit sám;
jak vypadá odpověď na vypršenou session, zatím nevíme.

## Endpointy

### `GET /uzivatel/{idUzivatele}/info`

Kompletní karta strávníka. **Pro integraci nedoporučuju** — vrací mimo jiné
číslo účtu, předčíslí, banku, variabilní a specifický symbol, telefon, e-mail
a adresu. Nic z toho v HA nechceš, natož v `recorder` databázi a zálohách.
Zůstatek je i v odpovědi na login.

Užitečná pole, kdyby přesto:

| Pole | Význam |
|---|---|
| `kontoProObjednavani` | disponibilní zůstatek (= `stavKonecMesice`) |
| `stavKonecMesice`, `stavNasledujiciMesic`, `stavMinulyMesic` | výhled po zúčtování |
| `zaloha`, `zapocitatZalohu`, `platitZaMesic` | režim plateb (`"AKTUALNI"`) |
| `debetKc`, `debetDnu` | povolený přečerp; 0 = nelze objednat do mínusu |
| `maxPocetPorci` | strop pro `mnozstvi` v objednávce |
| `blokovatWeb` | objednávání přes web zakázáno |

Všechny částky chodí jako **string** (`"-820"`, `"0.0000"`).

Klient skládal URL s dvojitým lomítkem (`.../<hash>//uzivatel/...`) a server to
tolerovat; jedno lomítko funguje taky.

### `GET /zarizeni/{idZarizeni}/dny/od/{od}/do/{do}`

Hlavní endpoint. Datum `YYYY-MM-DD`, vrací **pole dnů**.

```json
[
  {
    "datum": "2026-09-09",
    "den": {
      "castiDne": [
        {
          "nazev": "Oběd",
          "od": "11:40",
          "do": "14:00",
          "objednavky": {
            "3632660": {
              "idUzivatele": 3632660,
              "idMenu": "12222",
              "stav": "Prihlaseno",
              "mnozstvi": 1,
              "odebral": 0,
              "odeslana": true,
              "potvrzena": true,
              "zdroj": "Jidelna",
              "hodnoceno": 0,
              "smiOhodnotit": false
            }
          },
          "menu": [
            {
              "nazev": "1",
              "id": 12222,
              "lzeObjednat": true,
              "cenyVahy": [
                { "id": 3632660, "cena": "41.00", "hmotnost": null }
              ],
              "dataHodnoceni": { "pocetHlasu": 0, "potrebaHlasu": 7 },
              "chody": [
                { "nazev": "Polévka",  "jidlo": "Řecká polévka s rýží a vejci", "alergeny": ["3","7"] },
                { "nazev": "Jídlo",    "jidlo": "Vepřová kýta ala bažant",      "alergeny": ["1","12"] },
                { "nazev": "Příloha",  "jidlo": "dušená rýže",                  "alergeny": [] },
                { "nazev": "Doplněk",  "jidlo": "salátový bar",                 "alergeny": [] },
                { "nazev": "Nápoj",    "jidlo": "ochucená voda, voda, mléko",   "alergeny": ["7"] }
              ]
            }
          ]
        }
      ]
    }
  }
]
```

#### Poznámky k modelu

- **`castiDne` je pole** (snídaně / oběd / večeře). Go klient natvrdo bral
  `[0]`. Vybírej podle `nazev`.
- **`od` / `do`** je výdejní doba dané části dne. Pěkné pro senzor „výdej běží".
- **`objednavky` je mapa klíčovaná `idUzivatele`** — jedno volání vrátí stav pro
  všechny strávníky pod účtem. Ideální pro integraci s víc dětmi.
- **`cenyVahy` je taky per-strávník**, klíč `id`. Cena se liší podle věkové
  kategorie, takže **neber `cenyVahy[0]`**, hledej svoje `idUzivatele`.
  `hmotnost` bývá `null` (jídelna gramáže nevyplňuje).
- **Typy nejsou konzistentní:** `menu[].id` je číslo, `objednavky[].idMenu` je
  string, `objednavky[].idUzivatele` je číslo, ale klíč mapy je string. Při
  porovnávání přetypovávej.
- **`menu[].nazev`** bývá číslo varianty (`"1"`, `"2"`), ale může být i text
  (`"Salát"`). Popis jídla to není — ten si složíš z `chody`.
- **`chody: []`** = varianta se ten den nenabízí.
- `dataHodnoceni` a `hodnoceno`/`smiOhodnotit` je hodnocení jídel, pro
  automatizace nezajímavé.

#### Bez platné session: tichý degradovaný režim

**Nejzrádnější vlastnost celého API.** Volání bez cookie, s vypršenou nebo
s podvrženou session **nevrátí chybu** — vrátí HTTP 200 a platný JSON
s kompletním jídelníčkem. Chybí jen data vázaná na uživatele:

| Klíč | S přihlášením | Bez |
|---|---|---|
| `objednavky` | ano | **chybí celý klíč** |
| `cenyVahy` | ano | **chybí** |
| `dataHodnoceni` | ano | chybí |
| `menu`, `chody`, `alergeny`, `od`/`do` | ano | ano |

Jídelníček je tedy veřejný; přihlášení přidává osobní vrstvu. Neznámé i vypršené
session ID se chovají identicky — server jen založí novou anonymní session
(`Set-Cookie: PHPSESSID=...` v každé odpovědi).

**Důsledek pro integraci:** platnost session **nelze** poznat podle status kódu
ani podle `stav`. Testuj přítomnost klíče `objednavky` v odpovědi — jinak po
24 hodinách začne integrace tvrdit, že oběd není objednaný, s validními daty
a bez chyby v logu.

#### `lzeObjednat` neznamená „lze objednat teď"

U minulého dne (2026-09-04) měla varianta 1 `lzeObjednat: true`, varianta 2
`false` a prázdné `chody`. Vypadá to tedy jako **„tato varianta je ten den
v nabídce"**, ne jako stav uzávěrky. Go klient ho používal jako filtr
objednatelnosti — nespoléhal bych na to. Uzávěrku (typicky den předem do
určité hodiny) v datech zatím nevidím; možná je jinde v odpovědi, jinak se to
pozná až podle chyby z POSTu.

#### Stavy objednávky

| Pole | Objednáno | Odhlášeno |
|---|---|---|
| `stav` | `Prihlaseno` | `Odhlaseno` |
| `mnozstvi` | 1 | 0 |
| `idMenu` | `"12222"` | `"12222"` — **zůstává vyplněné** |
| `odeslana` / `potvrzena` | true | true |

`idMenu` se při odhlášení nemaže, takže **samotná přítomnost záznamu neznamená
objednaný oběd** — rozhoduje `stav`, případně `mnozstvi`.

`odeslana` = objednávka doputovala do jídelny, `potvrzena` = jídelna ji
potvrdila. Odpovídá to ikoně obálky na webu (objednáno online, ještě nestaženo).
Pro senzor „oběd je zajištěný" chceš `stav == "Prihlaseno" && potvrzena`.

`odebral` (0/1) = fyzicky vyzvednuto. `zdroj` = kde objednávka vznikla
(`"Jidelna"`; web/appka/terminál budou mít jinou hodnotu, *neověřeno*).

### `POST /zarizeni/{idZarizeni}/objednavky`

Objednání i odhlášení. Content-Type `application/x-www-form-urlencoded`,
jediné pole **`json`**, jehož hodnota je JSON pole jako string:

```
json=[{"idUzivatele":"3632660","idMenu":"12222","den":"2026-09-15","stav":"Prihlaseno","mnozstvi":1}]
```

| Pole | Typ | Poznámka |
|---|---|---|
| `idUzivatele` | string | z loginu |
| `idMenu` | string | `menu[].id` přetypované |
| `den` | string | `YYYY-MM-DD` |
| `stav` | string | `Prihlaseno` / `Odhlaseno` |
| `mnozstvi` | number | 1 při objednání, 0 při odhlášení; strop `maxPocetPorci` |

Odpověď: `{ "stav": "ok" }`.

**Odhlášení je odvozené z toho, jak vypadá uložený záznam, ne z odchyceného
požadavku.** Server teoreticky může na vstupu čekat něco jiného, než vrací.
Ověř buď v devtools při kliknutí na odhlášení ve webu, nebo zkusmým POSTem
na den, kde ti nevadí experimentovat — a **výsledek vždy zkontroluj novým
GETem**, `{"stav":"ok"}` sám o sobě není důkaz.

Payload je pole, takže jde nejspíš odeslat víc objednávek najednou (víc dnů,
víc strávníků) — *neověřeno*, klient vždy posílal jednoprvkové pole.

## Číselník alergenů

`alergeny` jsou kódy podle přílohy vyhlášky (EU 1169/2011), fixní seznam:

| | | | |
|---|---|---|---|
| 1 obiloviny s lepkem | 2 korýši | 3 vejce | 4 ryby |
| 5 arašídy | 6 sója | 7 mléko | 8 skořápkové plody |
| 9 celer | 10 hořčice | 11 sezam | 12 oxid siřičitý |
| 13 vlčí bob | 14 měkkýši | | |

Chodí jako **stringy** (`["3","7"]`), ne čísla.

## Poznámky k integraci pro HA

- Jeden `DataUpdateCoordinator` nad `dny/od/../do/..` pokryje všechno kromě
  zůstatku; ten vem z odpovědi na login. Interval v řádu hodin bohatě stačí.
- Rozsah bych volil od dneška na 14 dní dopředu.
- Senzory: dnešní a zítřejší oběd (název z `chody`), stav objednávky, zůstatek
  konta (`device_class: monetary`, `CZK`, převod ze stringu), binary sensor
  „výdej běží" z `od`/`do`.
- Alergeny jako atribut nebo binary sensor pro hlídané kódy.
- Celý týdenní jídelníček v atributu → vylučit z `recorder`u.
- Objednání a odhlášení jako služby; po zápisu si vynuť refresh coordinatoru.
- Po každém načtení ověř přítomnost `objednavky`; když chybí, znovu se přihlas
  a zopakuj dotaz. Bez toho vypršelá session projde jako prázdný jídelníček.
- Ošetři `blokovatWeb` a záporné konto s `debetKc: 0` — objednávka pak
  pravděpodobně selže a chceš to hlásit srozumitelně, ne jako generickou chybu.

## Co pořád nevíme

- podoba chybové odpovědi u zápisu (`tag` u chyby, jestli je HTTP taky 403 jako u
  loginu) — u čtení chyba nepřijde vůbec, viz degradovaný režim výše. U loginu už
  ověřeno, viz „Update 2026-09-24" výše.
- hlavní účet přes e-mail a víc strávníků
- kde je uzávěrka objednávek
- historie plateb a odběrů (endpoint neznámý)
- jestli POST přijímá víceprvkové pole
