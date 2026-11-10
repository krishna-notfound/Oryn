import type { Metadata } from "next";
import Link from "next/link";

export const metadata: Metadata = {
  title: "Privacy Policy · Oryn",
  description: "What Oryn stores, why, who processes it, and how to delete it.",
};

const UPDATED = "10 November 2026";
const CONTACT = "krishnagtkr@gmail.com";

const sections: { title: string; body: string[] }[] = [
  {
    title: "What Oryn is",
    body: [
      "Oryn is a voice-first AI companion. You talk, Oryn listens, thinks and answers out loud. It is an open-source project, not a medical, legal or financial service.",
    ],
  },
  {
    title: "What we collect",
    body: [
      "Account: if you continue as a guest, a random account id and the username you choose. If you sign in with Google, your Google account id, name and email address. We never see your Google password.",
      "Preferences you choose during onboarding and in Settings: preferred name, languages, interests, conversation style, voice, and whether memory and history are on.",
      "Conversation history: the text of your messages and Oryn's replies, only while “save history” is on (it can be turned off in Settings).",
      "Memories: short facts Oryn extracts to personalise future conversations, only if you explicitly turn memory on. You can view, edit and delete every memory.",
    ],
  },
  {
    title: "What we do not collect",
    body: [
      "We do not store recordings of your voice. Audio is streamed to the speech provider for transcription and discarded.",
      "We do not sell your data, show ads, or use your conversations to train models.",
      "Our server logs contain timings and ids, not what you said.",
    ],
  },
  {
    title: "Who processes your data",
    body: [
      "Gnani AI (Prisma speech-to-text and Timbre text-to-speech) receives your audio and Oryn's reply text to transcribe and speak them.",
      "Sarvam AI receives the conversation text needed to generate each reply.",
      "Supabase stores your account, preferences, history and consent records (with per-user access rules on every table).",
      "If memory is on, an embedding provider receives short memory texts to make them searchable.",
      "The app runs on Vercel (website) and Amazon Web Services in Mumbai (voice server).",
    ],
  },
  {
    title: "Your choices",
    body: [
      "Turn memory or history off at any time in Settings; Oryn stops using them immediately.",
      "Delete individual memories from the Memories page.",
      `To delete your account and everything linked to it, email ${CONTACT} from the address on the account (or include your guest username), and we will delete it within 30 days.`,
      "Guest accounts live on your device: signing out of a guest account without linking Google cannot be undone.",
    ],
  },
  {
    title: "Safety",
    body: [
      "Oryn is a supportive companion, not a therapist. If you are in crisis in India, please contact Tele-MANAS at 14416 or someone you trust.",
    ],
  },
  {
    title: "Changes and contact",
    body: [
      `If this policy changes we will update this page and the date below. Questions: ${CONTACT}.`,
    ],
  },
];

export default function PrivacyPage() {
  return (
    <main className="mx-auto min-h-dvh max-w-2xl px-6 py-12 text-on-surface">
      <Link href="/" className="type-label-md text-on-surface-variant underline-offset-4 hover:underline">
        ← Back to Oryn
      </Link>
      <h1 className="mt-6 text-3xl font-semibold">Privacy Policy</h1>
      <p className="type-body-md mt-2 text-on-surface-variant">Last updated {UPDATED}</p>
      {sections.map((section) => (
        <section key={section.title} className="mt-8">
          <h2 className="text-xl font-semibold">{section.title}</h2>
          {section.body.map((paragraph) => (
            <p key={paragraph} className="type-body-md mt-3 leading-relaxed text-on-surface-variant">
              {paragraph}
            </p>
          ))}
        </section>
      ))}
    </main>
  );
}
