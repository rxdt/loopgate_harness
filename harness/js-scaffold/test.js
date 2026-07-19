import assert from "node:assert";
import test from "node:test";
import { checkAnswer } from "./quiz.js";

test(" Quiz - checkAnswer function", () => {
	assert.strictEqual(checkAnswer(false), true);
	assert.strictEqual(checkAnswer(true), false);
});
