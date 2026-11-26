# Personal-data policy

What the reviewer flags, and the labelling guide for benchmark cases. Labels follow this
document. If a case doesn't fit a rule, change this document first (with the user's OK),
then the label, never the other way round.

GDPR Art. 4(1) decides what is personal data. This policy decides which of it is worth
flagging in a pull request. The two differ on purpose: flagging everything that is legally
personal data would bury real risks in noise.

## Tiers

| Tier | Examples | Flag? |
|---|---|---|
| **S: special categories** (Art. 9, 10) | health and diagnoses, biometrics, ethnicity, religion, sexual orientation, political opinion, union membership, criminal records | Always, in whichever of the 4 categories fits. Mark it in the case's `NOTES.md` line. |
| **A: direct identifiers** | email, phone, full name, postal address, national ID, passport, tax ID, IBAN, card number, date of birth, precise location, photo, voice | Yes, in the 4 categories |
| **B: online identifiers** | IP address, device ID, advertising ID, cookie ID | Only when sent to analytics events or a third party (rule 3) |
| **C: quasi-identifiers** | postcode, age, gender, job title, user agent | Not alone. Yes when combined with tier A in the same flow |
| **Not flagged** | internal surrogate IDs, keyed hashes and random tokens, masked values, aggregates, company data, fake data in tests | No |

## Rules

1. **Hashes.** A plain, unkeyed hash of an identifier (`sha256(email)`, `md5(phone)`) is
   still personal data: common values can be recovered by hashing a list and comparing.
   Flag it like the identifier itself. A keyed hash (HMAC with a secret) or a random token
   is a pseudonym and is not flagged.
2. **Internal IDs.** Logging or sending an internal ID (`user.id`, an account UUID, an
   order number) is not flagged.
3. **IP addresses.** Flag an IP when it goes into analytics events or to a third party.
   Ordinary request or access logging of the IP is not flagged.
4. **Masked values.** A value masked before it is logged (`j***@example.com`, last four
   digits of a phone) is not flagged.
5. **Company data.** Data about a legal person (company name, VAT number, company address)
   is not personal data (Recital 14) and is not flagged.
6. **Tests.** Data in tests, fixtures and seed files is not flagged when it is clearly fake
   (Faker, `example.com`, reserved number ranges).
7. **Whole objects.** Logging or sending a whole request body, user object or customer
   object counts as sending the personal data it contains.

## The four categories

**`PII_STORAGE`**: a new field holding tier S or A data with no encryption and no retention
rule. Encrypted at rest *and* covered by a retention rule: not flagged. Label the model or
schema definition as the required issue (Django model, Prisma schema, JPA entity). The
matching migration line is an optional issue.

**`PII_LOGGING`**: tier S or A data, or a tier B identifier in analytics (rule 3), written
to logs, error trackers or analytics events.

**`THIRD_PARTY_TRANSFER`**: tier S or A data, or a plain hash of it (rule 1), sent to an
outside service that the change introduces or newly sends it to. Label the payload or the
call that sends it.

**`ERASURE_REGRESSION`**: deletion or anonymisation of personal data removed or weakened:
a removed anonymise call, a deleted retention job, a hard delete turned into a soft delete
that keeps personal fields. Label the removed lines (`side: old`) when the change is a
removal, and the new lines when it replaces erasure with something weaker. Moving erasure
logic somewhere else while still calling it is not flagged.

## Labelling conventions

- One issue per distinct risk. Two fields added in one model in one go are one issue with an
  `end_line`.
- When the same risk shows in several places (model and migration, job file and its
  registration), the main place is required and the others are optional.
- Use `required: false` only for the cases above and for genuinely borderline risks, and give
  every one a `NOTES.md` line.
- Every value in a case is fake: `example.com` emails, US `555-01xx` or UK `07700 900xxx`
  phone numbers, the documentation example IBAN `DE89 3704 0044 0532 0130 00`, invented names.
- Diffs carry no hints: no file names, comments or strings that point at the label.
