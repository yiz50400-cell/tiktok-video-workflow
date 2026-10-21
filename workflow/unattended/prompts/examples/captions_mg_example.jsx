// 示例：TikTok 种草字幕 MG（顶部字幕 + 黑描边 + 半透明底 + 结尾 CTA）。CUTS 为每句起始帧（最后一个=总帧数），LINES 与镜头一一对应。
const CUTS = [0, 66, 126, 186, 246, 306, 369, 419, 467, 527];
const LINES = [
  "Pare de esfregar o banheiro de joelhos 😩",
  "Essa escova elétrica faz o trabalho por você 🙌",
  "Gira sozinha e tira a sujeira em segundos 🌀",
  "Funciona até na gordura do forno 🔥",
  "Pia, azulejo, box... limpa tudo ✅",
  "Vem com várias cabeças trocáveis 🔄",
  "Cabo extensível: sem dor nas costas 💪",
  "Azulejo brilhando sem esforço ✨",
  "Sem fio e recarregável 🔋",
];
const OUTLINE =
  "-4px -4px 0 #000, 4px -4px 0 #000, -4px 4px 0 #000, 4px 4px 0 #000, 0 4px 0 #000, 0 -4px 0 #000, 4px 0 0 #000, -4px 0 0 #000, 0 8px 18px rgba(0,0,0,0.55)";
export default function LegendasV3() {
  const frame = useCurrentFrame();
  let i = CUTS.findIndex((s, k) => frame >= s && frame < CUTS[k + 1]);
  if (i < 0) i = LINES.length - 1;
  const local = frame - CUTS[i];
  const pop = interpolate(local, [0, 4, 7], [0.82, 1.06, 1], { extrapolateRight: "clamp" });
  const op = interpolate(local, [0, 3], [0, 1], { extrapolateRight: "clamp" });
  const isHook = i === 0;
  const isCta = i === LINES.length - 1;
  const ctaPulse = isCta ? 1 + 0.05 * Math.sin(local / 3) : 1;
  return (
    <AbsoluteFill>
      <div style={{ position: "absolute", top: 250, left: 60, right: 60, display: "flex",
        justifyContent: "center", opacity: op, transform: `scale(${pop})` }}>
        <div style={{ background: "rgba(0,0,0,0.42)", borderRadius: 26, padding: "18px 30px",
          color: isHook ? "#FFE600" : "#ffffff", fontFamily: '"Montserrat", "Noto Sans", sans-serif',
          fontWeight: 900, fontSize: isHook ? 76 : 64, lineHeight: 1.15, textAlign: "center",
          textShadow: OUTLINE }}>
          {LINES[i]}
        </div>
      </div>
      {isCta && (
        <div style={{ position: "absolute", bottom: 380, left: 0, right: 0, textAlign: "center",
          transform: `scale(${ctaPulse})` }}>
          <span style={{ background: "#FF2D55", color: "#fff", fontFamily: '"Montserrat", sans-serif',
            fontWeight: 900, fontSize: 56, padding: "16px 36px", borderRadius: 40,
            textShadow: "0 3px 0 rgba(0,0,0,0.35)" }}>
            GARANTA A SUA NO CARRINHO!
          </span>
        </div>
      )}
    </AbsoluteFill>
  );
}
