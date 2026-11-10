// File names that mix Hebrew (or Arabic) with digits, like "02-חתונה_04m37s-10m08s.mp4",
// display scrambled: the bidi algorithm pulls the digits next to the Hebrew word into its
// right-to-left run ("02-04_חתונהm37s…"). Isolating each right-to-left run (FSI … PDI)
// keeps the rest of the name in reading order. For display only: never send the result
// to the server or use it as a path.

const RTL = "֐-ࣿיִ-﷿ﹰ-﻿";
// A run of right-to-left letters, with any spaces and marks between them.
const RUN = new RegExp(`[${RTL}](?:[${RTL}\\s\\u0300-\\u036F'"׳״]*[${RTL}])?`, "g");

export function ltr(text: string | null | undefined): string {
  if (!text) return text ?? "";
  return text.replace(RUN, (run) => `⁨${run}⁩`);
}
