# Public sharing hosts

Evidence collected on 2026-11-25. These registrations identify link formats;
they do not guarantee current product metadata, price, availability, ownership
of an affiliate account, or automatic affiliate conversion. The retailer count
remains 37. Hosts are exact; no wildcard or implicit `www` aliases were added.

## Newly registered hosts and public sources

| Host | Mapping | Public evidence |
| --- | --- | --- |
| `shopee.promo` | Shopee shortener | [Printer post](https://t.me/cacadoresofertas/78592), URL `https://shopee.promo/s/2SQsd4`; also documented by OrbitSender below. |
| `divulgador.magalu.com` | Magalu shortener | [PS5 post](https://t.me/gtOFERTAS/141020), URL `https://divulgador.magalu.com/sEaeLFJT`. |
| `magalu.promo` | Magalu shortener | [OrbitSender documentation](https://docs.orbitsender.com/docs/ajuda/configuracoes), section “Os links do OrbitSender (.promo) e domínio próprio”. |
| `magazineluiza.onelink.me` | Magalu app link | [Thermal paste post](https://t.me/ofertasbenevides/26850), URL `https://magazineluiza.onelink.me/589508454/c9h19gx1`. |
| `natura.promo` | Natura shortener | [OrbitSender documentation](https://docs.orbitsender.com/docs/ajuda/configuracoes), same explicit domain list. |
| `onelink.shein.com` | SHEIN shortener | [Brazilian product posts](https://t.me/s/sheindescontosmalucos?before=21), including `https://onelink.shein.com/4/4216oi9k96jn`; [SHEIN affiliate page](https://br.shein.com/campus--a-1500.html) also links this host. |
| `shein.top` | SHEIN shortener | [Public publisher post](https://t.me/s/rutube?before=1549) links `shein.top/gaow0qv` for a photo box; [public channel description](https://t.me/sheintopfinds) links `https://shein.top/hdltx93`. The older product post uses HTTP; tests use HTTPS and do not claim its present availability. |
| `m.shein.com` | SHEIN mobile storefront | [SHEIN FAQ](https://br.shein.com/faq/detail?id=87035) explicitly links `https://m.shein.com/br/tax-policy-a-3476.html`. This proves the host mapping, not a product on that policy page. |
| `share.temu.com` | Temu share link | [Brazilian product posts](https://t.me/s/fafaofertas?before=5232), including `https://share.temu.com/fcwTccaFnAA`. |
| `temu.to` | Temu shortener | [Same public posts](https://t.me/s/fafaofertas?before=5232), URL `https://temu.to/m/uav22pf66f1`, explicitly an app/bonus link rather than the product link. |
| `ofertou.ai` | Multi-store intermediary | [Razer mouse post](https://t.me/ofertasbenevides/26861), URL `https://ofertou.ai/CnkMj`; the observed 302 destination is Awin with a KaBuM! product URL. Registered only in `AFFILIATE_NETWORK_HOSTS`, never as a retailer. |

OrbitSender documents that its `.promo` shorteners lead to converted affiliate
links and can enable robot protection. Registering them does not grant access
through protection, login, or verification pages.

## Observed public responses

Requests were public GETs with `OrionProductPreview/1.0`, no credentials, no
cookies and no redirect following. A response ending at a verification or error
page was not retried through another identity or endpoint.

- `divulgador.magalu.com/sEaeLFJT` returned 302 to
  `https://www.magazineluiza.com.br/playstation-5-edicao-digital-825gb-1-controle-branco-sony/divulgador/oferta/240604800/ga/cnle/?promoter_id=1799284&partner_id=3440`.
- `ofertou.ai/CnkMj` returned 302 to
  `https://www.awin1.com/cread.php?awinmid=17729&awinaffid=1725617&ued=https://www.kabum.com.br/produto/715915/mouse-gamer-razer-deathadder-v3-30-000dpi-preto-rz0104640100`.
  The test's subsequent Awin-to-store response is a local fixture, not a claim
  that this entire chain was fetched during research.
- `onelink.shein.com/4/4216oi9k96jn` returned 200 with public product title and
  image metadata. Its HTML refers to a product on `br.shein.com` and retains
  `url_from` attribution. No script or redirect embedded in it was executed.
- `share.temu.com/fcwTccaFnAA` returned 307 to `https://www.temu.com`; this old
  link therefore did not itself supply a current product destination.
- `magazineluiza.onelink.me/589508454/c9h19gx1` returned 200 `text/plain` with
  body `OK`, which is not product metadata.
- The sampled `shein.top` and `temu.to` requests timed out. Their registrations
  are supported by the published links, not a claim of successful live import.
- `magalu.promo` and `natura.promo` are documented host registrations; no live
  product response for those hosts was claimed or used as a test fixture.

## Regression coverage

`test_shared_link_hosts.py` tests all new retailer hosts, rejects unproven `www`
and other subdomains and suffix spoofing, retains 37 unique retailer IDs, and
keeps intermediary links separate from retailer identification.

MockTransport chain tests use the public Magalu and Ofertou/Awin URLs above.
Product HTML and prices at the end of those chains are explicitly local test
data. They verify that source URLs and attribution parameters survive, TLS/Host
use the validated destination, no authentication is forwarded, unsafe or unknown
redirects are not requested, and no affiliate conversion is invented.

A final regression uses the observed Magalu `200 OK` text response to verify
that an accepted host without product data is not a successful product import.
