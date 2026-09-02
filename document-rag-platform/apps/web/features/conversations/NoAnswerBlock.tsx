import type { ChatResponse } from "../../lib/types";
export const NO_ANSWER_LABELS: Record<
  NonNullable<ChatResponse["no_answer_reason"]>,
  string
> = {
  smalltalk: "Bu mesaj belge sorgusu olarak değerlendirilmedi.",
  policy_refusal:
    "Kaynağın veri politikası bu modelle yanıt üretimine izin vermiyor.",
  insufficient_evidence:
    "Yüklediğin belgelerde doğrulanabilir yeterli kanıt bulunamadı.",
  provider_failure: "Yanıt sağlayıcısı isteği güvenli biçimde tamamlayamadı.",
  permission_denied: "Bu konuşma veya kaynak için erişim yetkin bulunmuyor.",
  malformed_response: "Model çıktısı doğrulama sözleşmesini geçemedi.",
};
export function NoAnswerBlock({
  reason,
}: {
  reason: ChatResponse["no_answer_reason"];
}) {
  return (
    <p
      role="status"
      className="rounded border border-ink-line bg-paper-dim p-3 text-sm text-ink-soft"
    >
      {reason
        ? NO_ANSWER_LABELS[reason]
        : "Bu soruya doğrulanabilir bir yanıt üretilemedi."}
    </p>
  );
}
