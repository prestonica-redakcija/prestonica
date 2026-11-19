# Prestonica: autonomni news portal

Portal se sam osvežava na svakih 15 minuta: preuzima vesti iz 17 srpskih izvora sa obe strane spektra, spaja istu priču iz više medija u jednu vest, rangira je po tvojim prioritetima (politika > ekonomija > društvo > svet > ostalo), Claude je piše po uređivačkoj politici, sistem bira legalnu fotografiju i objavljuje ceo sajt sa pretragom.

```
RSS izvori ──> grupisanje ──> rangiranje ──> Claude piše ──> fotografija ──> sajt + pretraga ──> GitHub Pages
   (15 min)      (ista priča)   (tvoji prioriteti)  (uređivačka politika)  (arhiva/Commons/Unsplash)
```

## Kako upravljaš portalom

| Šta želiš | Gde menjaš |
|---|---|
| Uređivačka linija, ton, vrednosti, zabrane | `config/urednicka-politika.md` |
| Prioriteti rubrika, koliko vesti na naslovnoj, limiti | `config/portal.yaml` |
| Koji mediji se prate, koliko im veruješ | `config/izvori.yaml` |
| Teme koje guramo ili preskačemo | `config/teme.yaml` |
| Sopstvene fotografije | `uploads/foto/` + `uploads/index.yaml` |
| Autorski tekst | Telegram bot ili fajl u `inbox/` |

Fajlove menjaš direktno na GitHub-u (olovka "Edit"), ili mi kažeš u razgovoru šta da promenim.

### Telegram komande (samo sa tvog naloga)
- duži tekst (300+ znakova) ili fotografija sa opisom: objavljuje se kao autorski tekst, tvojim glasom
- `/skini <deo naslova>`: vest se skida sa sajta u sledećem osvežavanju
- `/vrati <deo naslova>`: vraća skinutu vest
- `/istakni <deo naslova>`: vest ide u glavnu poziciju na 12 sati
- `/tema <tema>`: portal 48 sati posebno prati temu
- `/status`: stanje sistema

## Pokretanje (jednokratno, oko 30 minuta)

1. **Domen**: registruj `prestonica.rs` kod bilo kog ovlašćenog registra (lista na rnids.rs).
2. **GitHub**: napravi javni repozitorijum `prestonica` i postavi ovaj kod (javni repo = besplatni GitHub Actions minuti).
3. **Tajni ključevi**: Settings → Secrets and variables → Actions → New repository secret:
   - `ANTHROPIC_API_KEY` (console.anthropic.com)
   - `UNSPLASH_ACCESS_KEY` (unsplash.com/developers, besplatno)
   - `TELEGRAM_TOKEN` (napravi bota kod @BotFather) i `TELEGRAM_CHAT_ID` (tvoj ID, npr. preko @userinfobot)
4. **Pages**: Settings → Pages → Source: *GitHub Actions*. Custom domain: `prestonica.rs`, uključi *Enforce HTTPS*.
5. **DNS** kod registra: A zapisi `185.199.108.153`, `185.199.109.153`, `185.199.110.153`, `185.199.111.153` i CNAME `www` → `<tvoj-github-nalog>.github.io`.
6. **Prvo pokretanje**: Actions → "Osvežavanje portala" → *Run workflow*. Posle toga ide samo, na 15 minuta.

## Interna aplikacija "Komanda" (ikonica na telefonu)

Adresa: `prestonica.rs/statistika.html`. Zaključana PIN-om, isključena iz Google-a (`robots.txt`), radi kao aplikacija kad se doda na početni ekran. Pet kartica u jednoj aplikaciji:

| Kartica | Šta pokazuje | Odakle podaci |
|---|---|---|
| **Portal** | pregledi, posetioci, vreme čitanja, pročitanost, najčitaniji tekstovi, izvori, zemlje, gradovi, uređaji, novi i povratnici, uživo | sopstveno merenje: brojač u `templates/base.html` → Worker → D1 |
| **Google** | GA4 pregledi/korisnici/sesije i Search Console klikovi, impresije, CTR, pozicije, upiti, strane | Google Analytics 4 Data API + Search Console API |
| **Video** | pretplatnici, pregledi po dnevniku, lajkovi, komentari | YouTube Data API |
| **X** | pratioci, impresije, angažovanje, poređenje link-objava i kartica | X API v2 |
| **Produkcija** | koliko tekstova dnevno, po rubrici i izvoru, stanje ciklusa od 15 minuta | `produkcija.json` iz builda → Worker |

### Postavljanje servisa (jednokratno)

Servis je `cloudflare/stat-worker.js`, ruta `prestonica.rs/api/stat*`. Na Cloudflare-u:

1. **Baza**: Workers & Pages → D1 → *Create database*, ime `prestonica-stat`. Tabele se prave same pri prvom pozivu.
2. **Worker**: *Create Worker*, ime `prestonica-stat`, nalepi sadržaj `cloudflare/stat-worker.js`.
3. **Veza sa bazom**: Settings → Bindings → D1 database, ime promenljive **`DB`** → `prestonica-stat`.
4. **Ruta**: Settings → Domains & Routes → *Add route* → `prestonica.rs/api/stat*`, zona `prestonica.rs`.
5. **Tajne** (Settings → Variables and Secrets → *Encrypt*):

| Tajna | Obavezna | Šta je |
|---|---|---|
| `STAT_KLJUC` | da | PIN za ulaz u aplikaciju |
| `STAT_SO` | da | bilo kakav dug slučajan niz; so za otiske posetilaca |
| `STAT_UPIS` | za Produkciju | isti niz dodati i kao GitHub secret `STAT_UPIS` |
| `GOOGLE_SA_EMAIL`, `GOOGLE_SA_KEY` | za Google | servisni nalog: e-adresa i privatni ključ (ceo PEM, sa `-----BEGIN PRIVATE KEY-----`) |
| `GA4_PROPERTY_ID` | za Google | broj imanja iz GA4 (Admin → Property Settings) |
| `GSC_SITE_URL` | ne | podrazumevano `https://prestonica.rs/` |
| `YT_API_KEY`, `YT_CHANNEL_ID` | za Video | API ključ i ID kanala (`UC...`) |
| `X_BEARER` | za X | Bearer token iz X Developer portala |

### Google strana (nalog `redakcija@prestonica.rs`)

1. Google Cloud Console → novi projekat → uključi **Google Analytics Data API** i **Search Console API**.
2. IAM → Service Accounts → novi nalog → Keys → *Add key* → JSON. Iz fajla uzmi `client_email` i `private_key`.
3. GA4 → Admin → Property Access Management → dodaj tu e-adresu kao **Viewer**.
4. Search Console → Settings → Users and permissions → dodaj tu e-adresu kao **Full** ili **Restricted**.

Search Console podaci kasne 1-3 dana; aplikacija to piše na kartici da pad ne bi izgledao kao pad saobraćaja.

### Dodavanje na telefon

iPhone: otvori adresu u Safariju → *Podeli* → **Dodaj na početni ekran**. Android (Chrome): meni ⋮ → **Instaliraj aplikaciju**. Posle toga se otvara bez adresne linije, PIN se unosi jednom.

Aplikacija pamti PIN u pregledaču, drži ljusku u kešu da se otvara trenutno, a brojeve nikad ne kešira. Portal se osvežava na 30 sekundi (uživo) i 5 minuta (izveštaj), Google na 10 minuta, YouTube i X na 30 minuta zbog kvota.

## Lokalno testiranje
```
pip install -r requirements.txt
python -m engine.run --fixtures tests/fixtures/items.json --max 60   # bez interneta i API-ja
python -m engine.run --mock                                         # pravi izvori, probni tekstovi
python -m engine.run                                                # pravi rad (traži ANTHROPIC_API_KEY)
```

## Pravna pravila ugrađena u sistem
- Tekstovi se pišu svojim rečima, uz navođenje i link ka svakom izvoru. Doslovni citati su kratki.
- `robots.txt` svakog medija se poštuje (zato Politika, Vreme i RSE nisu u izvorima).
- Fotografije drugih medija se nikad ne preuzimaju; koriste se tvoja arhiva, Wikimedia Commons (slobodne licence) i Unsplash, uz potpis autora i oznaku "Ilustracija".
- Stranica "O nama" javno objašnjava da tekstove priprema automatizovani sistem.
- Pre lansiranja: popuni impresum u `config/portal.yaml` i proveri sa advokatom upis u Registar medija (APR).
