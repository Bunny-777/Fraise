import { DocCover, Icon, type IconName } from "./icons";

type Suggestion = { icon: IconName; label: string; text: string; accent?: boolean };
type Doc = { name: string; chunks: number; kind: "document" | "recording" };

const AUDIO = /\.(mp3|m4a|mp4|wav|aac|flac|ogg|opus|webm|mov|amr)$/i;

export const QUOTE = "The best interface is a conversation you forget you're having.";

const RESTING: Suggestion[] = [
  { icon: "search", label: "Research", text: "Research the best SDE internships and make me a deck", accent: true },
  { icon: "web", label: "Compare", text: "Compare the best electric cars under 20 lakhs" },
  { icon: "remember", label: "Memory", text: "Remember I prefer window seats" },
  { icon: "math", label: "Quick math", text: "What's 18% of 240?" },
];

function titleOf(filename: string): string {
  const dot = filename.lastIndexOf(".");
  const stem = dot > 0 ? filename.slice(0, dot) : filename;
  return stem.length > 24 ? `${stem.slice(0, 23)}…` : stem;
}

function suggestionsFor(docs: Doc[]): Suggestion[] {
  if (!docs.length) return RESTING;
  const title = titleOf(docs[0].name);
  if (docs[0].kind === "recording") {
    return [
      { icon: "doc", label: "Debrief", text: `Go through ${title} and pull out the action items`, accent: true },
      { icon: "search", label: "Ask", text: `What did we decide on ${title}?` },
      { icon: "web", label: "Research", text: "Research what that call was about" },
      { icon: "recall", label: "Recall", text: "What have I asked you to remember?" },
    ];
  }
  return [
    { icon: "doc", label: "Summarize", text: `Summarize ${title}`, accent: true },
    { icon: "search", label: "Ask", text: `What does ${title} say about…?` },
    { icon: "web", label: "Research", text: "Research this topic and write it up" },
    { icon: "recall", label: "Recall", text: "What have I asked you to remember?" },
  ];
}

interface BoardProps {
  docs: Doc[];
  uploading: string;
  receded: boolean;
}

export default function Board({ docs, uploading, receded }: BoardProps) {
  const suggestions = suggestionsFor(docs);
  const hasDocs = docs.length > 0 || !!uploading;

  return (
    <div className={`board${receded ? " receded" : ""}`} aria-hidden={receded}>
      {hasDocs && (
        <section className="board-section">
          <h2 className="board-label">Your files</h2>
          <div className="docs-masonry">
            {uploading && (
              <div className="card doc-card pending">
                <DocCover name={uploading} />
                <div className="card-meta">
                  <span className="meta-name">{uploading}</span>
                  <span className="chunks">{AUDIO.test(uploading) ? "transcribing…" : "adding…"}</span>
                </div>
              </div>
            )}
            {docs.map((d) => (
              <div key={d.name} className="card doc-card">
                <DocCover name={d.name} />
                <div className="card-meta">
                  <span className="meta-name">{d.name}</span>
                  <span className="chunks">
                    {d.kind === "recording" ? `recording · ${d.chunks} passages` : `${d.chunks} passages`}
                  </span>
                </div>
              </div>
            ))}
          </div>
        </section>
      )}

      <section className="board-section">
        <div className="suggest-list">
          {suggestions.map((s) => (
            <button key={s.text} className={`suggest-row${s.accent ? " accent" : ""}`} aria-label={s.label}>
              <Icon name={s.icon} />
              <span className="suggest-text">{s.text}</span>
            </button>
          ))}
        </div>
      </section>
    </div>
  );
}
