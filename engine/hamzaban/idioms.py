"""English idioms and set phrases the translation model gets wrong.

TranslateGemma translates the common ones well («Break a leg» -> «موفق باشی»),
but renders others word by word or even backwards (measured 1 Oct 2026 on 60
idioms: «Big deal» -> «مهم است», «When pigs fly» -> «وقتی خوک‌ها پرواز کنند»,
«Roger that, over» -> «خداحافظ»). For those:

* a sentence that is only the idiom («Copy that.», «Big deal!») gets its
  Persian equivalent directly, without the model. These short ones are never
  touched inside a longer sentence («copy the file», «whatever you want»,
  «Roger» the name);
* the others (Persian part None) are replaced inside any sentence by their
  plain English meaning before translation, which the model then gets right.

The user can add more in ~/Library/Application Support/Hamzaban/idioms.txt:

    english idiom = plain English meaning = معادل فارسی

(the Persian part is optional; regular expressions are allowed in the idiom).
"""
import re
import threading
from pathlib import Path

from .config import APP_HOME

IDIOMS_FILE = APP_HOME / "idioms.txt"
WORDS_FILE = Path(__file__).parent / "words" / "idioms.txt"  # the user's fixes, kept in the code too

# (idiom regex, plain meaning, Persian equivalent when the idiom is the whole sentence)
BUILTIN = [
    # radio, military, action films
    (r"roger(?: that)?(?:,? over)?", "understood", "دریافت شد"),
    (r"copy(?: that)?", "understood", "دریافت شد"),
    (r"wilco", "understood, will do", "دریافت شد، انجام می‌دهم"),
    (r"over and out", "end of message", "تمام"),
    (r"fire in the hole", "take cover, explosion", "بخوابید، انفجار!"),
    (r"lock and load", "get your weapons ready", "سلاح‌ها آماده!"),
    (r"(?:we've |we have |i've |i have )?got (?:a )?man down", "someone is wounded", "یه نفر زخمی شده!"),
    (r"cover me", "protect me while I move", "پوششم بده!"),
    (r"hold (?:the|your) fire", "stop shooting", "شلیک نکنید!"),
    (r"stand down", "stop and step back", "عقب‌نشینی کنید"),
    (r"move,? move,? move", "go quickly", "برید، برید، برید!"),
    (r"brace,? brace(?:,? brace)?", "prepare for impact", "آماده‌ی برخورد!"),
    (r"mayday(?:,? mayday)*", "emergency, help", "کمک! وضعیت اضطراری!"),
    (r"we've got company", "enemies are coming", "داره یکی میاد!"),
    (r"(?:it's |its )?go time", "it's time to start", "وقتشه!"),
    (r"eyes on", "I can see it", "دیدمش"),
    # short replies people say all the time
    (r"big deal", "that's not important", "مهم نیست"),
    (r"no big deal", "it's not a problem", "مشکلی نیست"),
    (r"beats me", "I don't know", "نمی‌دونم"),
    (r"my bad", "my mistake", "تقصیر من بود"),
    (r"you bet", "of course", "حتماً!"),
    (r"no way,? jos[eé]", "absolutely not", "عمراً!"),
    (r"get lost", "go away", "گم شو!"),
    (r"knock it off", "stop doing that", "بس کن!"),
    (r"cut it out", "stop doing that", "بس کن!"),
    (r"spit it out", "say it now", "زود باش بگو!"),
    (r"get a grip", "control yourself", "به خودت بیا!"),
    (r"cool your jets", "calm down", "آروم باش!"),
    (r"chill out", "calm down", "آروم باش!"),
    (r"hang in there", "don't give up", "طاقت بیار"),
    (r"suit yourself", "do what you want", "هر جور راحتی"),
    (r"fair enough", "that is reasonable", "باشه، قبول"),
    (r"so be it", "then let it be so", "باشه، پس همین"),
    (r"bottoms up", "let's drink", "به سلامتی!"),
    (r"cheers", "thank you / to your health", "به سلامتی!"),
    (r"speak of the devil", "we were just talking about you", "حلال‌زاده‌ست!"),
    (r"long time no see", "it's been a long time since we met", "چه عجب! خیلی وقته ندیدمت"),
    (r"break a leg", "good luck", "موفق باشی!"),
    (r"bless you", "health to you", "عافیت باشه"),
    (r"good riddance", "I'm glad it's gone", "خس‌وخاشاک کم!"),
    (r"you nailed it", "you did it perfectly", "ترکوندی!"),
    (r"nailed it", "did it perfectly", "ترکوندم!"),
    (r"i'm all ears", "I'm listening carefully", "سراپا گوشم"),
    (r"hold your horses", "wait a moment, be patient", "صبر کن ببینم!"),
    (r"take it easy", "relax, calm down", "آروم باش، سخت نگیر"),
    (r"my lips are sealed", "I won't tell anyone", "از من چیزی درز نمی‌کنه"),
    (r"you're telling me", "I know that very well", "به کی می‌گی!"),
    (r"tell me about it", "I agree completely", "به کی می‌گی!"),
    (r"don't mention it", "you're welcome", "قابلی نداشت"),
    (r"it's on me", "I will pay", "مهمون من"),
    (r"keep the change", "you can keep the extra money", "بقیه‌اش مال خودت"),
    (r"after you", "you go first", "بفرمایید"),
    (r"same here", "me too", "منم همین‌طور"),
    (r"you don't say", "really? (sarcastic)", "نه بابا!"),
    (r"whatever", "I don't care", "هر چی"),
    (r"gotcha", "I understand", "گرفتم"),
    (r"no hard feelings", "I'm not angry", "به دل نگیر"),
    (r"(?:it's |its )?a piece of cake", "it's very easy", "مثل آب خوردنه"),
    (r"(?:that's |it's )?the last straw", "that is more than I can bear", "دیگه کاسه‌ی صبرم لبریز شد"),
    (r"when pigs fly", "never", "عمراً! وقتی خر بیاره باقالی"),
    (r"once in a blue moon", "very rarely", "هر از گاهی، خیلی کم"),
    (r"(?:it's )?raining cats and dogs", "it's raining very heavily", "بارون شدیدی می‌باره"),
    (r"rome wasn't built in a day", "important things take time", "کارها یه‌شبه درست نمی‌شن"),
    (r"every cloud has a silver lining", "something good comes from every bad situation", "در ناامیدی بسی امید است"),
    (r"the walls have ears", "someone may be listening", "دیوار موش داره، موش هم گوش داره"),
    (r"actions speak louder than words", "what you do matters more than what you say", "دو صد گفته چون نیم کردار نیست"),
    (r"better late than never", "it's better to do it late than not at all", "ماهی رو هر وقت از آب بگیری تازه‌ست"),
    (r"easy come,? easy go", "what you get easily you lose easily", "باد آورده رو باد می‌بره"),
    (r"no pain,? no gain", "you must work hard to succeed", "نابرده رنج گنج میسر نمی‌شود"),
    (r"practice makes perfect", "practice makes you skilled", "کار نیکو کردن از پر کردن است"),
    (r"look before you leap", "think before you act", "اول فکر کن، بعد عمل کن"),
    (r"curiosity killed the cat", "being too curious can be dangerous", "فضولی موقوف!"),
    (r"don't count your chickens before they hatch", "don't be sure before it happens",
     "جوجه رو آخر پاییز می‌شمرن"),
    (r"you can't have your cake and eat it(?: too)?", "you can't have both things at once",
     "هم خدا رو می‌خوای هم خرما رو؟"),
    (r"birds of a feather flock together", "similar people stay together", "کبوتر با کبوتر، باز با باز"),
    (r"out of sight,? out of mind", "people forget what they don't see", "از دل برود هر آنکه از دیده برفت"),
    (r"the early bird catches the worm", "those who start early succeed", "سحرخیز باش تا کامروا باشی"),
    (r"let sleeping dogs lie", "don't bring up an old problem", "شر به پا نکن، بذار گذشته‌ها بگذره"),
    (r"it takes two to tango", "both people are responsible", "یه دست صدا نداره"),
    (r"there's no place like home", "home is the best place", "هیچ جا خونه‌ی آدم نمی‌شه"),
    (r"time flies", "time passes quickly", "زمان مثل برق و باد می‌گذره"),
    (r"(?:well,? )?that's that", "it is finished", "تموم شد و رفت"),
    (r"so far so good", "everything is fine until now", "تا اینجا که خوب بوده"),
    (r"better safe than sorry", "it's better to be careful", "احتیاط شرط عقله"),
    (r"(?:it's )?not rocket science", "it's not complicated", "کار حضرت فیل نیست"),
    (r"(?:it's )?all greek to me", "I don't understand it at all", "هیچی ازش سر در نمیارم"),
    (r"you scratch my back(?:,? (?:and )?i'll scratch yours)?", "if you help me, I'll help you",
     "دست، دست رو می‌شوره"),
    (r"bring home the bacon", "earn money for the family", "نون‌آور خونه بودن"),
    (r"(?:i'll |i will )?take a rain check", "maybe another time", "باشه برای یه وقت دیگه"),
    (r"hit the sack", "go to sleep", "برو بخواب"),
    (r"hit the hay", "go to sleep", "برو بخواب"),
    (r"hit the road", "leave now", "راه بیفت برو"),
    (r"let's call it a day", "let's stop working for today", "برای امروز بسه"),
    (r"let's roll", "let's go", "بریم!"),
    (r"stop beating around the bush", "stop avoiding the main point", "این‌قدر طفره نرو"),
    (r"bite your tongue", "don't say that", "زبونت رو گاز بگیر!"),
    (r"keep your chin up", "stay positive, don't lose hope", "سرت رو بالا بگیر، ناامید نشو"),
    (r"cut me some slack", "be less strict with me", "یه‌کم کوتاه بیا"),
    # idioms inside longer sentences: replaced by their meaning before translation
    (r"spill(?:s|ed|ing)? the beans", "reveal the secret", None),
    (r"let(?:s|ting)? the cat out of the bag", "reveal the secret", None),
    (r"cost(?:s|ing)? an arm and a leg", "be very expensive", None),
    (r"kick(?:s|ed|ing)? the bucket", "die", None),
    (r"pushing up daisies", "dead", None),
    (r"raining cats and dogs", "raining very heavily", None),
    (r"hold (?:your|their) horses", "wait and be patient", None),
    (r"when pigs fly", "never", None),
    (r"rome wasn't built in a day", "important things take time", None),
    (r"speak(?:ing)? of the devil", "we were just talking about that person", None),
    (r"call(?:s|ed|ing)? it a day", "stop working for today", None),
    (r"hit(?:s|ting)? the (?:sack|hay)", "go to sleep", None),
    (r"hit(?:s|ting)? the road", "leave", None),
    (r"bark(?:s|ed|ing)? up the wrong tree", "blaming the wrong person", None),
    (r"pull(?:s|ed|ing)? (?:my|your|his|her|our|their) leg", "joke with someone", None),
    (r"bit(?:e|es|ing)? the bullet", "accept something difficult and do it", None),
    (r"the ball is in (?:your|my|his|her|our|their) court", "it's that person's turn to decide", None),
    (r"out of the woods", "safe from danger", None),
    (r"under the weather", "a little sick", None),
    (r"beat(?:s|ing)? around the bush", "avoid the main point", None),
    (r"(?:that|the) ship has sailed", "that opportunity is gone", None),
    (r"on thin ice", "in a risky situation", None),
    (r"throw(?:s|ing|n)? (?:me|you|him|her|us|them) under the bus", "blame someone to save oneself", None),
    (r"threw (?:me|you|him|her|us|them) under the bus", "blamed someone to save himself", None),
    (r"get (?:our|your|their|my) ducks in a row", "get organized", None),
    (r"couch potato", "lazy person who watches TV all day", None),
    (r"cold feet", "fear about doing something", None),
    (r"(?:my|your|his|her|our|their) hands are tied", "cannot do anything about it", None),
    (r"off the rails", "out of control", None),
    (r"jump(?:s|ed|ing)? the gun", "act too soon", None),
    (r"in the same boat", "in the same situation", None),
    (r"once in a blue moon", "very rarely", None),
    (r"a piece of cake", "very easy", None),
    (r"the last straw", "the final thing I can bear", None),
    (r"(?:my|your|his|her) two cents", "opinion", None),
    (r"on the same page", "in agreement", None),
    (r"touch(?:ing)? base", "contact each other briefly", None),
    (r"circle back", "return to this topic later", None),
    (r"heads[- ]up", "advance warning", None),
    (r"ballpark (?:figure|estimate|number)", "rough estimate", None),
    (r"low[- ]hanging fruit", "easy tasks", None),
    (r"think outside the box", "think creatively", None),
    (r"back to square one", "back to the beginning", None),
    (r"the elephant in the room", "the obvious problem nobody talks about", None),
    (r"a blessing in disguise", "a good thing that seemed bad at first", None),
    (r"(?:at|by) the drop of a hat", "immediately", None),
    (r"(?:to )?add insult to injury", "to make a bad situation worse", None),
    (r"burn(?:s|ed|t|ing)? the midnight oil", "work late into the night", None),
    (r"(?:it's |it is )?the tip of the iceberg", "only a small part of the problem", None),
    (r"miss(?:es|ed|ing)? the boat", "lose the opportunity", None),
    (r"in hot water", "in trouble", None),
    (r"(?:a )?wild goose chase", "a hopeless search", None),
    (r"steal(?:s|ing)? the show", "attract the most attention", None),
    (r"stole the show", "attracted the most attention", None),
    (r"(?:right )?on the money", "exactly correct", None),
    (r"hit(?:s|ting)? the nail on the head", "say exactly the right thing", None),
    (r"go(?:es|ing)? the extra mile", "make more effort than needed", None),
    (r"went the extra mile", "made more effort than needed", None),
    (r"(?:to )?call the shots", "to make the decisions", None),
    (r"face the music", "accept the consequences", None),
    (r"(?:to )?get out of hand", "to become uncontrollable", None),
    (r"got out of hand", "became uncontrollable", None),
    (r"(?:to )?keep an eye on", "to watch carefully", None),
    (r"by the skin of (?:my|your|his|her|our|their) teeth", "only just, barely", None),
    (r"(?:to )?let off steam", "to release anger or stress", None),
    (r"(?:to )?break the ice", "to start a friendly conversation", None),
    (r"broke the ice", "started a friendly conversation", None),
    (r"under the table", "secretly and illegally", None),
    (r"(?:to )?pay through the nose", "to pay far too much", None),
    (r"(?:to )?go down in flames", "to fail completely", None),
    (r"(?:to )?have a chip on (?:my|your|his|her|their) shoulder", "to feel resentful", None),
    (r"(?:a )?dead ringer for", "someone who looks exactly like", None),
    (r"(?:to )?be in the doghouse", "to be in trouble with someone", None),
    (r"in the doghouse", "in trouble with someone", None),
    (r"(?:to )?go cold turkey", "to stop suddenly", None),
    (r"(?:the )?whole nine yards", "everything", None),
    (r"(?:up )?shit creek", "in a very bad situation", None),
    (r"(?:a )?loose cannon", "an unpredictable person", None),
    (r"(?:to )?have (?:my|your|his|her|our|their) back", "to protect and support someone", None),
    (r"got (?:my|your|his|her|our|their) back", "will protect and support", None),
]

_lock = threading.Lock()
_cache = {"mtime": object(), "rules": None}
_EDGE_PUNCT = " \t.,!?;:'\"«»“”…-—"


def _compile(entries):
    """-> [(inline regex or None, whole-sentence regex, meaning, Persian)]"""
    rules = []
    for pattern, meaning, fa, inline in entries:
        try:
            rx = re.compile(rf"(?<![\w']){pattern}(?![\w'])", re.IGNORECASE) if inline else None
            whole = re.compile(rf"^{pattern}$", re.IGNORECASE)
        except re.error:
            continue
        rules.append((rx, whole, meaning, fa))
    return rules


def _mtime(path):
    try:
        return path.stat().st_mtime
    except OSError:
        return None


def _rules():
    mtime = (_mtime(IDIOMS_FILE), _mtime(WORDS_FILE))
    with _lock:
        if _cache["rules"] is not None and _cache["mtime"] == mtime:
            return _cache["rules"]
        entries = []
        for path in (IDIOMS_FILE, WORDS_FILE):  # the user's own come first: they win
            try:
                for line in path.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    parts = [x.strip() for x in line.split("=")]
                    fa = parts[2] if len(parts) > 2 and parts[2] else None
                    if len(parts) >= 2 and parts[0] and (parts[1] or fa):
                        # the user's: whole sentence always; inside sentences only with a plain meaning
                        entries.append((parts[0], parts[1] or None, fa, bool(parts[1])))
            except OSError:
                pass
        rules = _compile(entries + [(p, m, fa, fa is None) for p, m, fa in BUILTIN])
        _cache.update(mtime=mtime, rules=rules)
        return rules


def whole(text: str) -> str | None:
    """Persian for a sentence that is nothing but an idiom («Copy that.», «Big deal!»)."""
    core = text.strip(_EDGE_PUNCT)
    if len(core) > 60:
        return None
    for _rx, whole_rx, _meaning, fa in _rules():
        if fa and whole_rx.match(core):
            return fa
    return None


def whole_meaning(text: str) -> str | None:
    """A short sentence that is an idiom plus a name («Roger that, Major.»): the
    plain meaning with the name, for the model («understood, Major.»)."""
    m = re.match(r"^(.{1,40}?),\s*([A-Z]\w*(?:\s[A-Z]\w*)?)([.!?]*)$", text.strip())
    if not m:
        return None
    for _rx, whole_rx, meaning, _fa in _rules():
        if meaning and whole_rx.match(m.group(1).strip(_EDGE_PUNCT)):
            return f"{meaning}, {m.group(2)}{m.group(3) or '.'}"
    return None


def plain(text: str) -> str:
    """The sentence with every idiom replaced by its plain meaning (for the model)."""
    out = whole_meaning(text) or text
    for rx, _whole, meaning, _fa in _rules():
        if rx is not None:
            out = rx.sub(meaning, out)
    return out
