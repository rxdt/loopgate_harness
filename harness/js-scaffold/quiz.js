export function checkAnswer(answer) {
	const correctAnswer = false;
	return answer === correctAnswer;
}
function testQuiz() {
	const trueBtn = document.getElementById("btn-true");
	const falseBtn = document.getElementById("btn-false");
	trueBtn.addEventListener("click", () => {
		const isCorrect = checkAnswer(true);
		trueBtn.style.background = isCorrect ? "#d4edda" : "#f8d7da";
		falseBtn.style.background = "";
	});
	falseBtn.addEventListener("click", () => {
		const isCorrect = checkAnswer(false);
		falseBtn.style.background = isCorrect ? "#d4edda" : "#f8d7da";
		trueBtn.style.background = "";
	});
}
if (typeof document !== "undefined") {
	testQuiz();
}
