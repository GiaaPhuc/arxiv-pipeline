"""M2-B3 sentence-segmentation fixtures; synthetic offline tests."""

import pytest

from parsing.sentence_splitter import split_sentences


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("One sentence.", ["One sentence."]),
        ("A. B.", ["A.", "B."]),
        ("First! Second? Third.", ["First!", "Second?", "Third."]),
        ("", []),
        ("  ", []),
        ("No final dot", ["No final dot"]),
        ("Dr. Smith wrote it. It works.", ["Dr. Smith wrote it.", "It works."]),
        ("See Fig. 1 for details. Next.", ["See Fig. 1 for details.", "Next."]),
        ("We use e.g. Python. It works.", ["We use e.g. Python.", "It works."]),
        ("As shown by et al. in 2024. Next.", ["As shown by et al. in 2024.", "Next."]),
        ("The value is 3.14159. Done.", ["The value is 3.14159.", "Done."]),
        ("Use p < 0.05. Done.", ["Use p < 0.05.", "Done."]),
        ("A. Einstein studied this. Indeed.", ["A. Einstein studied this.", "Indeed."]),
        ("J. K. Rowling wrote it. Next.", ["J. K. Rowling wrote it.", "Next."]),
        (r"See \cite{smith.2020,doe}. Next.", [r"See \cite{smith.2020,doe}.", "Next."]),
        (r"Refer to \ref{fig.1}. Then proceed.", [r"Refer to \ref{fig.1}.", "Then proceed."]),
        (r"Compute $x. y$. Next.", [r"Compute $x. y$.", "Next."]),
        (r"We solve \(x. y\). Next.", [r"We solve \(x. y\).", "Next."]),
        (r"\begin{equation}x.y\end{equation} Next.", [r"\begin{equation}x.y\end{equation} Next."]),
        (
            "Visit https://arxiv.org/abs/2408.11045. Then return.",
            ["Visit https://arxiv.org/abs/2408.11045.", "Then return."],
        ),
        (r"Visit \url{https://a.b/c.d}. Next.", [r"Visit \url{https://a.b/c.d}.", "Next."]),
        ("U.S. research grows. Next.", ["U.S. research grows.", "Next."]),
        ("Results are in Sec. 3. Next.", ["Results are in Sec. 3.", "Next."]),
        ("We report 1.2.3. Next.", ["We report 1.2.3.", "Next."]),
        ("Hello world.   More text.", ["Hello world.", "More text."]),
        ("Hi!", ["Hi!"]),
        ("What?", ["What?"]),
        ("Words without punctuation", ["Words without punctuation"]),
        (
            r"\href{https://a.b/a.1}{the source}. Next.",
            [r"\href{https://a.b/a.1}{the source}.", "Next."],
        ),
    ],
)
def test_split(source, expected):
    assert split_sentences(source) == expected


def test_no_data_loss():
    source = r"See Fig. 1. Eq. (2) is $x.y$. Read \cite{a.b}. Continue."
    result = split_sentences(source)
    assert " ".join(result) == source
