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
