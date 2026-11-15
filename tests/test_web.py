"""The demo plays each sentence as soon as it is made.

The demo's own JavaScript is run with node: the player with a stand-in for the browser's audio clock, the
worker's speak() with a stand-in for the model. Skipped when node is not installed.
"""

import json
from pathlib import Path
import shutil
import subprocess
import unittest

PUBLIC = Path(__file__).resolve().parents[1] / "web" / "public"

PLAYER = r"""
const fs = require("fs");
const livePlayer = new Function(fs.readFileSync(process.argv[1], "utf8") + "\nreturn livePlayer;")();
const RATE = 24000;

// In place of the browser's AudioContext: it notes what is started and when, and the test moves its clock.
// "refused": the browser does not let the page make sound. "slow": the sound has not started yet.
function fakeContext(kind = "running") {
  const context = {
    currentTime: 0, state: kind === "running" ? "running" : "suspended", destination: {}, started: [], woken: 0,
    resume() { context.woken += 1; return kind === "refused" ? Promise.reject(new Error("not allowed")) : new Promise(() => {}); },
    createBuffer(channels, length, sampleRate) {
      const data = new Float32Array(length);
      return { length, sampleRate, getChannelData: () => data };
    },
    createBufferSource() {
      const source = { buffer: null, onended: null, to: null, when: null, over: false, cut: false,
        connect(node) { source.to = node; },
        start(when) { source.when = when; context.started.push(source); },
        stop() { if (!source.over) { source.over = true; source.cut = true; if (source.onended) source.onended(); } } };
      return source;
    },
    pass(seconds) {
      context.currentTime += seconds;
      for (const source of context.started) {
        if (!source.over && source.when + source.buffer.length / source.buffer.sampleRate <= context.currentTime) {
          source.over = true;
          if (source.onended) source.onended();
        }
      }
    },
  };
  return context;
}
const tone = (length, level) => Float32Array.from({ length }, (_, i) => level * Math.sin(i / 7));
const same = (a, b) => a.length === b.length && a.every((value, i) => value === b[i]);
const starts = (context) => context.started.map((source) => source.when);
const later = () => new Promise((resolve) => setTimeout(resolve, 1));
const facts = {};

(async () => {
{ // Sentences that are made faster than they are spoken.
  const context = fakeContext();
  context.currentTime = 3.25;
  let changes = 0;
  const player = livePlayer(context, () => { changes += 1; });
  const idle = player.playing;
  player.begin();
  const waiting = player.playing;
  const clips = [tone(24000, 0.1), tone(36000, 0.2), tone(12000, 0.3)];
  player.add(clips[0], RATE); context.pass(0.2);
  player.add(clips[1], RATE); context.pass(0.1);
  player.add(clips[2], RATE);
  const played = player.end();
  const before = changes;
  context.pass(2.4);
  const nearTheEnd = player.playing;
  context.pass(0.4);
  facts.early = { idle, waiting, starts: starts(context), played, nearTheEnd, after: player.playing, told: changes > before,
                  intact: context.started.every((source, i) => same(source.buffer.getChannelData(0), clips[i])),
                  rates: context.started.map((source) => source.buffer.sampleRate),
                  toTheSpeakers: context.started.every((source) => source.to === context.destination) };
}
{ // A sentence that is made after the one before it has ended.
  const context = fakeContext();
  const player = livePlayer(context);
  player.begin();
  player.add(tone(24000, 0.1), RATE); context.pass(1.5);
  const dry = player.playing;
  player.add(tone(24000, 0.1), RATE);
  player.add(tone(0, 0.1), RATE);
  facts.late = { starts: starts(context), dry };
}
{ // Stop, more sentences of the stopped request, then a new request.
  const context = fakeContext();
  const player = livePlayer(context);
  player.begin();
  player.add(tone(24000, 0.1), RATE); player.add(tone(24000, 0.1), RATE); context.pass(0.5);
  player.stop();
  const cut = context.started.map((source) => source.cut);
  const stopped = player.playing;
  player.add(tone(24000, 0.1), RATE);
  const ignored = context.started.length;
  const played = player.end();
  context.pass(4);
  player.begin();
  player.add(tone(24000, 0.1), RATE);
  facts.stop = { cut, stopped, ignored, played, again: starts(context).slice(2), playing: player.playing };
}
{ // Stop before the first sentence is made.
  const context = fakeContext();
  const player = livePlayer(context);
  player.begin();
  player.stop();
  player.add(tone(24000, 0.1), RATE);
  facts.soon = { played: player.end(), started: context.started.length, playing: player.playing };
}
{ // A new request while the last one is still being spoken.
  const context = fakeContext();
  const player = livePlayer(context);
  player.begin();
  player.add(tone(48000, 0.1), RATE); context.pass(0.5);
  player.begin();
  player.add(tone(24000, 0.1), RATE);
  facts.again = { cut: context.started.map((source) => source.cut), starts: starts(context) };
}
{ // The browser refuses the sound; and a request that brought no audio.
  const context = fakeContext("refused");
  const player = livePlayer(context);
  player.begin();
  await later();
  player.add(tone(24000, 0.1), RATE); player.add(tone(24000, 0.1), RATE);
  const played = player.end();
  const quiet = livePlayer(fakeContext());
  quiet.begin();
  facts.off = { woken: context.woken, played, cut: context.started.map((source) => source.cut), playing: player.playing,
                nothing: quiet.end() };
}
{ // The sound is slow to start: a short text is done before the browser's clock runs.
  const context = fakeContext("slow");
  const player = livePlayer(context);
  player.begin();
  await later();
  player.add(tone(24000, 0.1), RATE);
  const played = player.end();
  const waiting = player.playing;
  const cut = context.started.map((source) => source.cut);
  context.state = "running";
  context.pass(1.2);
  facts.slow = { played, waiting, cut, starts: starts(context), after: player.playing };
}
console.log(JSON.stringify(facts));
})();
"""

WORKER = r"""
const fs = require("fs");
const source = fs.readFileSync(process.argv[1], "utf8");
const take = (pattern) => source.match(pattern)[0];
const code = [take(/const ABBREVIATIONS = .*;/), take(/function sentences\(text\) \{[\s\S]*?\n\}/),
              take(/function finished\(text\) \{[\s\S]*?\n\}/), take(/const PEAK = .*;/),
              take(/function limitPeak\(samples\) \{[\s\S]*?\n\}/), take(/async function speak\(\{[\s\S]*?\n\}/),
              "return speak;"].join("\n");

// In place of the phonemizer and the model: one utterance for a sentence, a tone as its audio, loud when the
// sentence says so. Messages are passed on the way the browser does it: what is handed over is gone here.
const wave = (text) => Float32Array.from({ length: 400 + 100 * text.length },
                                         (_, i) => (text.includes("loud") ? 1.2 : 0.5) * Math.sin(i / 5 + 1.5));
const order = [];
const messages = [];
const fit = (sentence) => [{ text: sentence, ids: [1], stress: [0] }];
const synthesize = async (phones) => { order.push("made " + phones.text); return wave(phones.text); };
const postMessage = (message, transfer = []) => {
  order.push(message.type === "sentence" ? "sent " + message.phonemes : message.type);
  messages.push(structuredClone(message, { transfer }));
};
const speak = new Function("fit", "synthesize", "postMessage", "config", code)(fit, synthesize, postMessage, { sample_rate: 24000 });
const peak = (samples) => samples.reduce((most, value) => Math.max(most, Math.abs(value)), 0);

speak({ id: 7, text: process.argv[2], voice: 0, speed: 1 }).then(() => {
  const heard = messages.filter((message) => message.type === "sentence");
  const done = messages[messages.length - 1];
  const raw = heard.map((message) => wave(message.phonemes));
  const joined = Float32Array.from(raw.flatMap((samples) => Array.from(samples)));
  const scale = 0.99 / peak(joined);
  console.log(JSON.stringify({
    order,
    sentences: heard.map((message, i) => ({ id: message.id, index: message.index, total: message.total, rate: message.sampleRate,
                                            length: message.audio.length, peak: peak(message.audio),
                                            untouched: message.audio.every((value, j) => value === raw[i][j]) })),
    done: { type: done.type, id: done.id, rate: done.sampleRate, length: done.audio.length, peak: peak(done.audio),
            farthest: done.audio.reduce((most, value, i) => Math.max(most, Math.abs(value - joined[i] * scale)), 0) },
  }));
});
"""


def node(script, *arguments):
    done = subprocess.run(["node", "-e", script, *arguments], capture_output=True, text=True, encoding="utf-8")
    if done.returncode:
        raise AssertionError(done.stderr)
    return json.loads(done.stdout)


@unittest.skipUnless(shutil.which("node"), "needs node to run the demo's own code")
class LivePlayerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.facts = node(PLAYER, str(PUBLIC / "player.js"))

    def assertTimes(self, times, expected):
        self.assertEqual(len(times), len(expected))
        for time, wanted in zip(times, expected):
            self.assertAlmostEqual(time, wanted, places=9)

    def test_each_sentence_starts_where_the_one_before_it_ends(self):
        early = self.facts["early"]
        # The clock stands at 3.25 s. The first sentence starts 0.05 s from then, and the 1.0 s and 1.5 s of audio
        # that follow leave no gap and do not overlap.
        self.assertTimes(early["starts"], [3.3, 4.3, 5.8])
        self.assertTrue(early["intact"] and early["toTheSpeakers"])
        self.assertEqual(early["rates"], [24000, 24000, 24000])

    def test_it_plays_from_the_request_to_the_end_of_the_last_sentence(self):
        early = self.facts["early"]
        self.assertFalse(early["idle"])
        self.assertTrue(early["waiting"])                               # before the first sentence is made
        self.assertTrue(early["played"])
        self.assertTrue(early["nearTheEnd"])                            # the worker is done, the voice is not
        self.assertFalse(early["after"])
        self.assertTrue(early["told"])                                  # the page hears about it and resets its button

    def test_a_late_sentence_starts_right_away(self):
        # Made at 1.5 s, when the first one ended at 1.05 s: it does not start in the past. Empty audio is left out.
        self.assertTimes(self.facts["late"]["starts"], [0.05, 1.55])
        self.assertTrue(self.facts["late"]["dry"])

    def test_stop_silences_the_request_and_what_it_still_sends(self):
        stop = self.facts["stop"]
        self.assertEqual(stop["cut"], [True, True])
        self.assertFalse(stop["stopped"])
        self.assertEqual(stop["ignored"], 2)
        self.assertTrue(stop["played"])                                 # the page must not start the whole clip instead
        self.assertTimes(stop["again"], [4.55])
        self.assertTrue(stop["playing"])

    def test_stop_before_the_first_sentence_is_a_stop_too(self):
        self.assertEqual(self.facts["soon"], {"played": True, "started": 0, "playing": False})

    def test_a_new_request_cuts_the_one_before_it(self):
        self.assertEqual(self.facts["again"]["cut"], [True, False])
        self.assertTimes(self.facts["again"]["starts"], [0.05, 0.55])    # not after the 2 s that were cut

    def test_the_page_is_told_when_there_is_nothing_to_hear(self):
        off = self.facts["off"]
        self.assertEqual(off["woken"], 1)
        self.assertFalse(off["played"])                                 # the page then plays the whole clip, as it used to
        self.assertEqual(off["cut"], [True, True])                      # and the queue cannot start by itself later
        self.assertFalse(off["playing"])
        self.assertFalse(off["nothing"])

    def test_a_slow_start_is_not_a_refusal(self):
        slow = self.facts["slow"]
        self.assertTrue(slow["played"])                                 # the text was done before the sound was running
        self.assertTrue(slow["waiting"])
        self.assertEqual(slow["cut"], [False])
        self.assertTimes(slow["starts"], [0.05])                        # and it is heard as soon as the clock runs
        self.assertFalse(slow["after"])


@unittest.skipUnless(shutil.which("node"), "needs node to run the demo's own code")
class WorkerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.facts = node(WORKER, str(PUBLIC / "worker.js"), "One. Two is loud. Three")

    def test_each_sentence_is_sent_before_the_next_one_is_made(self):
        self.assertEqual(self.facts["order"], ["made One.", "sent One.", "made Two is loud.", "sent Two is loud.",
                                               "made Three.", "sent Three.", "done"])

    def test_each_sentence_comes_with_its_audio_below_full_scale(self):
        sentences = self.facts["sentences"]
        self.assertEqual([(s["id"], s["index"], s["total"], s["rate"]) for s in sentences],
                         [(7, 0, 3, 24000), (7, 1, 3, 24000), (7, 2, 3, 24000)])
        self.assertEqual([s["length"] for s in sentences], [800, 1600, 1000])
        self.assertEqual([s["untouched"] for s in sentences], [True, False, True])
        self.assertAlmostEqual(sentences[1]["peak"], 0.99, places=5)     # 1.2 as the model made it

    def test_the_whole_clip_still_comes_at_the_end(self):
        done = self.facts["done"]
        self.assertEqual((done["type"], done["id"], done["rate"], done["length"]), ("done", 7, 24000, 3400))
        self.assertAlmostEqual(done["peak"], 0.99, places=5)
        self.assertLess(done["farthest"], 1e-6)                          # every sentence scaled by the same factor


if __name__ == "__main__":
    unittest.main()
