"""Sequential batch context and pronoun memory ledger for long-video subtitle translation.

Maintains narrative context, confirmed character address rules, and boundary dialogue
cues across sequential batches when translating long videos that exceed a single request.
"""

from __future__ import annotations

from collections import Counter
import re


class RollingContextLedger:
    """Maintains narrative context, confirmed character address rules, and boundary dialogue cues across sequential batches."""

    def __init__(self, base_context: str = "") -> None:
        self.base_context: str = str(base_context or "").strip()
        self.confirmed_rules: list[str] = []
        self.preceding_cues: list[tuple[str, str]] = []
        self._seen_rule_signatures: set[str] = set()

    def add_confirmed_rule(self, rule: str) -> None:
        rule_clean = str(rule or "").strip()
        if not rule_clean:
            return
        sig = rule_clean.lower()
        if sig not in self._seen_rule_signatures:
            self._seen_rule_signatures.add(sig)
            self.confirmed_rules.append(rule_clean)

    def set_preceding_cues(self, cues: list[tuple[str, str]]) -> None:
        self.preceding_cues = [
            (str(s).strip(), str(t).strip())
            for s, t in cues
            if str(s).strip() or str(t).strip()
        ]


def update_ledger_from_batch(
    ledger: RollingContextLedger,
    source_batch: list[str],
    translated_batch: list[str],
) -> None:
    """Update confirmed address rules and boundary cues after translating a batch."""
    if not source_batch or not translated_batch:
        return

    # 1. Update boundary cues (save the last 3-4 translated lines of the batch)
    pairs = [
        (s.strip(), t.strip())
        for s, t in zip(source_batch, translated_batch)
        if s.strip() and t.strip()
    ]
    if pairs:
        ledger.set_preceding_cues(pairs[-4:])

    # 2. Extract and reinforce confirmed relationship rules
    combined_src = " ".join(source_batch)
    combined_trn = " ".join(translated_batch)
    trn_lower = combined_trn.lower()

    # Rule: Upperclassman (学长 / 学姐)
    if "学长" in combined_src:
        if "anh" in trn_lower or "học trưởng" in trn_lower:
            ledger.add_confirmed_rule(
                "学长 (Học trưởng / Upperclassman) -> Xưng hô bắt buộc: 'anh' - 'em'"
            )
    if "学姐" in combined_src:
        if "chị" in trn_lower or "học tỷ" in trn_lower:
            ledger.add_confirmed_rule(
                "学姐 (Học tỷ / Senior female) -> Xưng hô bắt buộc: 'chị' - 'em'"
            )
    if "学妹" in combined_src or "学弟" in combined_src:
        ledger.add_confirmed_rule(
            "学妹 / 学弟 (Đàn em / Junior) -> Xưng hô bắt buộc: 'em'"
        )

    # Rule: Aggressive confrontation / Stalker (找死 / 老子 / mày - tao)
    has_aggressive_src = any(k in combined_src for k in ("找死", "老子", "混蛋", "滚开", "放开", "狗东西", "给脸不要脸", "你等着", "这玩意", "问你了吗", "现男友"))
    has_aggressive_trn = "mày" in trn_lower or "tao" in trn_lower or "cô cứ đợi đấy" in trn_lower
    if has_aggressive_src or has_aggressive_trn:
        ledger.add_confirmed_rule(
            "Antagonist / Confrontation / Kẻ bám đuôi -> Xưng hô đối kháng: 'mày - tao' hoặc 'cô/hắn - tôi'. Tuyệt đối cấm xưng 'em' với kẻ bám đuôi hay dịch hăm dọa thành 'em cứ đợi đấy'."
        )

    # Rule: Banter "哥们" / "兄弟"
    if "哥们" in combined_src or "兄弟" in combined_src:
        if any(w in trn_lower for w in ("cô nương", "em gái", "bạn à", "cô em")):
            ledger.add_confirmed_rule(
                "哥们 / 兄弟 (hướng về nữ hoặc trêu đùa) -> Xưng hô: 'cô nương' / 'em gái' / 'bạn' (tuyệt đối không dịch 'ông bạn' hay 'người anh em')"
            )

    # Rule: Workplace / Honorifics
    if "老板" in combined_src or ("总" in combined_src and any(f"{c}总" in combined_src for c in "张李王赵刘陈杨黄周吴")):
        if "sếp" in trn_lower or "giám đốc" in trn_lower:
            ledger.add_confirmed_rule(
                "老板 / ...总 (Lãnh đạo / Sếp) -> Xưng hô bắt buộc: 'sếp' / 'giám đốc'"
            )
    if "师傅" in combined_src or "师父" in combined_src:
        ledger.add_confirmed_rule(
            "师傅 / 师父 -> Xưng hô bắt buộc: 'sư phụ' / 'thầy' / 'bác tài'"
        )

    # Generic pronoun pattern extraction from translated output.
    # Catches pronoun choices the AI established that are not covered by the hardcoded keyword list above.
    _DISTINCTIVE_FIRST_PERSON = ("em", "tao", "mày", "con", "cháu")
    trn_words = trn_lower.split()
    first_person_counts = Counter(w for w in trn_words if w in _DISTINCTIVE_FIRST_PERSON)
    dominant = first_person_counts.most_common(1)
    if dominant:
        pronoun, count = dominant[0]
        if count >= 3:
            ledger.add_confirmed_rule(
                f"Batch pronoun pattern: nhân vật xưng '{pronoun}' (xuất hiện {count} lần trong batch này) — duy trì ở các batch tiếp theo."
            )


def build_rolling_context_guidance(
    ledger: RollingContextLedger,
    preceding_cues: list[tuple[str, str]] | None = None,
) -> str:
    """Format full context injection for batch N, including running ledger and boundary cues."""
    sections: list[str] = []

    # 1. Base pre-scan context
    if ledger.base_context:
        sections.append(ledger.base_context.strip())

    # 2. Confirmed address memory from preceding batches
    if ledger.confirmed_rules:
        rules_text = "\n".join(f"- {r}" for r in ledger.confirmed_rules)
        sections.append(
            "### Confirmed Address Memory from Preceding Batches (Xưng hô đã xác lập ở các đoạn trước):\n"
            f"{rules_text}\n"
            "CRITICAL: Maintain strict pronoun continuity with these established character address forms."
        )

    # 3. Preceding dialogue context (tail of previous batch)
    cues_to_use = preceding_cues if preceding_cues is not None else ledger.preceding_cues
    if cues_to_use:
        cues_formatted = []
        for i, (src, trn) in enumerate(cues_to_use, 1):
            cues_formatted.append(f"[{i}] Source: \"{src}\" -> Translated: \"{trn}\"")
        cues_block = "\n".join(cues_formatted)
        sections.append(
            "### Preceding Dialogue Context (Bối cảnh các câu thoại liền trước - để đảm bảo tính liên tục và đúng xưng hô):\n"
            f"{cues_block}\n\n"
            "CRITICAL INSTRUCTION FOR CONTEXT:\n"
            "- The above preceding cues are provided for narrative continuity and speaker tone/pronoun reference ONLY.\n"
            "- Do NOT re-translate or include these preceding cues in your output.\n"
            "- Translate ONLY the numbered dialogue lines requested in the prompt."
        )

    return "\n\n".join(sections).strip()


def learn_dialogue_context(
    *,
    source_segments: list[dict],
    polisher,
    src_lang: str = "zh-Hans",
    target_lang: str = "vi",
    max_cues: int = 300,
    user_guidance: str = "",
    existing_context: str = "",
) -> str:
    """Extract durable character profiles and strict two-way address rules (address_rules).

    Inspired by novel-ai-trans learn.md, tailored for spoken video dialogue:
    - If video has <= max_cues (default 300), analyzes 100% of the transcript.
    - If video has > max_cues, samples the first 200 cues to establish initial ground truth.
    - If Speaker Diarization tags ([SPEAKER_XX]) are present, maps speaker IDs directly to character profiles.
    - If user_guidance is provided, injects explicit user corrections and instructions into the prompt.
    - If existing_context is provided, provides the previous draft to let AI revise based on feedback.
    """
    if not source_segments or len(source_segments) < 3 or not hasattr(polisher, "generate_text"):
        return ""

    if len(source_segments) <= max_cues:
        sample_segs = source_segments
    else:
        sample_segs = source_segments[:200]

    lines = []
    has_speaker = False
    for idx, seg in enumerate(sample_segs, 1):
        txt = str(seg.get("original_text") or seg.get("text") or "").strip()
        spk = str(seg.get("metadata", {}).get("speaker") or seg.get("speaker") or "").strip()
        if spk:
            has_speaker = True
            lines.append(f"{idx}. [{spk}]: {txt}")
        else:
            lines.append(f"{idx}. {txt}")

    user_msg = "\n".join(lines)

    speaker_clause = (
        "Cues contain speaker tags like [SPEAKER_00]. IMPORTANT: Note that automated diarization often merges multiple individuals of the same gender into the same tag (e.g. [SPEAKER_00] could contain both the male lead and an antagonist/stalker/rival). Analyze the dialogue cues deeply to identify all distinct characters even if tags are shared.\n"
        if has_speaker
        else "Identify all interacting characters, their genders, and social roles from dialogue cues.\n"
    )

    feedback_and_context_clause = ""
    clean_guidance = str(user_guidance or "").strip()
    clean_existing = str(existing_context or "").strip()

    if clean_existing:
        feedback_and_context_clause += f"""
PREVIOUS DRAFT OF CHARACTER PROFILES (TO BE REVISED):
\"\"\"
{clean_existing}
\"\"\"
"""

    if clean_guidance:
        feedback_and_context_clause += f"""
CRITICAL USER CORRECTIONS & GUIDANCE (HIGHEST PRIORITY):
The user reviewed the character profiles and provided the following instruction:
"{clean_guidance}"

MANDATORY RULES FOR APPLYING USER FEEDBACK:
- You MUST strictly follow and prioritize the user's guidance above all else.
- If the user indicates that two characters or their roles/genders are reversed or swapped (e.g. "ngược", "đảo lại", "hoán đổi"), SWAP their roles, genders, and addressing rules completely!
- Override any previous inferences with the user's explicit corrections.

"""

    system_prompt = f"""You are an expert dialogue and script analyzer.
Analyze these {src_lang}->{target_lang} subtitle cues to extract a durable Character Profile and strict Two-Way Address Rules (quy tắc xưng hô 2 chiều) to ensure 100% address consistency.

{speaker_clause}{feedback_and_context_clause}Extract:
1. CHARACTERS & ROLES:
   - Identify Name / Title / Nickname (standard Hán-Việt for Chinese names, e.g. 张伟 -> Trương Vỹ, 李明 -> Lý Minh)
   - Gender & social role: Nam/Nữ, e.g. Sinh viên năm 3 (học trưởng), năm 1 (học muội), bạn trai cũ / kẻ bám đuôi (antagonist), sếp, đồng nghiệp...
   - Distinguish if multiple characters of the same gender share a speaker tag.

2. TWO-WAY ADDRESS RULES (Quy tắc xưng hô 2 chiều - address_rules):
   For each interacting pair:
   - Character A addresses B as (gọi) and refers to self as (xưng)
   - Character B addresses A as (gọi) and refers to self as (xưng)
   - Specify the exact pronoun pair: e.g. anh - em, mày - tao, tôi - anh/cô, cậu - mình...
   - For Romantic / Senior-Junior pairs (e.g. Học trưởng - Học muội): anh - em (cấm tự ý đổi thành tôi - cậu).
   - For Confrontation / Antagonist / Stalker / Harasser pairs:
     * Opponent/Stalker speaks with hostility, threats, or interrogation (e.g. "你等着", "找死", "找个这玩意", "你跟他确认关系了"): Xưng hô đối kháng: 'mày - tao' hoặc 'cô - tôi'. Tuyệt đối cấm xưng 'em' hay gọi thân mật.
     * Character confronting stalker/harasser (e.g. '这就是我现男友', '你听着没'): Phải xưng 'tôi' (hoặc 'tao'), gọi 'anh/mày'. Tuyệt đối cấm xưng 'em' với kẻ bám đuôi/lưu manh.
   - Strict constraints: Prevent inversion (chống lật ngôi, cấm tự ý xưng tôi - cậu hay gọi nữ là ông bạn/người anh em).

OUTPUT FORMAT:
Return ONLY concise, clear rules in {target_lang} (under 250 words), starting immediately with '### Hồ sơ nhân vật & Quy tắc xưng hô bắt buộc:'. No greetings, no preamble, no markdown backticks.
IMPORTANT: Do NOT use LaTeX math symbols, math mode, or dollar signs (NO $\\leftrightarrow$, NO $\\rightarrow$, NO $\\to$). Always use standard arrows like '↔' or '->'.
"""

    try:
        context = polisher.generate_text(
            system_msg=system_prompt.strip(),
            user_msg=user_msg,
            max_tokens=600,
            timeout=45,
        )
        return clean_dialogue_context(context)
    except Exception as e:
        print(f"[AI Translation] Warning: Dialogue learning failed ({e}), continuing with standard translation.")
        return ""


def clean_dialogue_context(text: str) -> str:
    """Clean up LaTeX math symbols like $\\leftrightarrow$ or $\\rightarrow$ that LLMs sometimes generate."""
    if not text:
        return ""
    replacements = [
        (r"\$\s*\\(?:long)?leftrightarrow\s*\$", " ↔ "),
        (r"\\(?:long)?leftrightarrow", " ↔ "),
        (r"\$\s*\\(?:long)?rightarrow\s*\$", " → "),
        (r"\\(?:long)?rightarrow", " → "),
        (r"\$\s*\\to\s*\$", " → "),
        (r"\\to\b", " → "),
        (r"\$\s*\\(?:long)?leftarrow\s*\$", " ← "),
        (r"\\(?:long)?leftarrow", " ← "),
        (r"\$\s*\\(?:Rightarrow|implies)\s*\$", " ⇒ "),
        (r"\\(?:Rightarrow|implies)", " ⇒ "),
        (r"\$\s*\\Leftarrow\s*\$", " ⇐ "),
        (r"\\Leftarrow", " ⇐ "),
    ]
    result = text
    for pat, rep in replacements:
        result = re.sub(pat, rep, result)
    result = re.sub(r" {2,}", " ", result)
    return result.strip()

