"""M2-B1/B2 offline contract tests: no network, no data files."""

import pytest

from parsing.cleaner import clean_latex_content, extract_and_expand_macros
from parsing.math_normalizer import normalize_math


@pytest.mark.parametrize(
    ("preamble", "body", "expected"),
    [
        (r"\newcommand{\R}{\mathbb{R}}", r"x\in\R", r"x\in\mathbb{R}"),
        (r"\newcommand{\vect}[1]{\mathbf{#1}}", r"\vect{x}", r"\mathbf{x}"),
        (r"\newcommand{\pair}[2]{(#1,#2)}", r"\pair{a}{b}", "(a,b)"),
        (r"\newcommand{\triple}[3]{#1/#2/#3}", r"\triple{a}{b}{c}", "a/b/c"),
        (r"\def\ABC{value}", r"\ABC", "value"),
        (r"\renewcommand{\foo}{NEW}", r"\foo", "NEW"),
        (r"\providecommand{\foo}{OK}", r"\foo", "OK"),
        (r"\newcommand{\foo}[4]{NO}", r"\foo{a}{b}{c}{d}", r"\foo{a}{b}{c}{d}"),
        (r"\newcommand{\section}{wrong}", r"\section{Intro}", r"\section{Intro}"),
        (r"\newcommand{\self}{\self}", r"\self", r"\self"),
        (r"\newcommand{\foo}[1]{#1}", r"\foo", r"\foo"),
        ("", r"\unknown{a}", r"\unknown{a}"),
    ],
)
def test_macros(preamble, body, expected):
    assert extract_and_expand_macros(preamble, body) == expected


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        (r"\centering Text", "Text"),
        (r"\textbf{bold {inner}}", "bold {inner}"),
        (r"\textit{A \textbf{B}}", "A B"),
        (r"\emph{Important}", "Important"),
        (r"\smallskip Hello", "Hello"),
        (r"\vspace{4pt}X", "X"),
        (r"\hspace*{1em} X", "X"),
        (r"\cline{1-3}Data", "Data"),
        (r"\begin{figure}[htbp!]\caption{a}", r"\begin{figure}\caption{a}"),
        (r"\begin{table}[H]A", r"\begin{table}A"),
        (r"See \citep{a, b, a}.", r"See \cite{a,b}."),
        (r"See \citet[see][p. 3]{a, b}.", r"See \cite{a,b}."),
        (r"\ref{fig:one} \cite{key}", r"\ref{fig:one} \cite{key}"),
        (" A   B\tC ", "A B C"),
        (r"\textsf{hello} and \texttt{world}", "hello and world"),
        (r"\LargeWord x", r"\LargeWord x"),
        (r"\Huge Big \normalsize and \HUGE giant", "Big and giant"),
    ],
)
def test_clean(source, expected):
    assert clean_latex_content(source) == expected


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        (r"\( x + y \)", "$x + y$"),
        (r"\begin{math}x\end{math}", "$x$"),
        (r"\ensuremath{a+b}", "$a+b$"),
        ("$ x + y $", "$x + y$"),
        (r"\[ x+y \]", r"\begin{equation}x+y\end{equation}"),
        ("$$ x+y $$", r"\begin{equation}x+y\end{equation}"),
        (r"\begin{align*}a&=b\end{align*}", r"\begin{equation}a&=b\end{equation}"),
        (r"\begin{gather}a\\b\end{gather}", r"\begin{equation}a\\b\end{equation}"),
        (r"\begin{displaymath}x\end{displaymath}", r"\begin{equation}x\end{equation}"),
        (
            r"\begin{equation*}x\label {eq:a}\notag\end{equation*}",
            r"\begin{equation}x\end{equation}",
        ),
        (r"\begin{multline}x\nonumber\end{multline}", r"\begin{equation}x\end{equation}"),
        (r"\begin{eqnarray}x\end{eqnarray}", r"\begin{equation}x\end{equation}"),
        (r"Price \$50 and $x$", r"Price \$50 and $x$"),
        (r"\verb|$x$| then $y$", r"\verb|$x$| then $y$"),
        (r"\url{http://x.org/$a$} and \(z\)", r"\url{http://x.org/$a$} and $z$"),
        (
            r"\href{http://x.org/$a$}{anchor} and \(z\)",
            r"\href{http://x.org/$a$}{anchor} and $z$",
        ),
        (r"\begin{verbatim}$x$\end{verbatim}", r"\begin{verbatim}$x$\end{verbatim}"),
        (r"\( a+\frac{b}{c} \)", "$a+\\frac{b}{c}$"),
    ],
)
def test_math(source, expected):
    assert normalize_math(source) == expected


def test_math_idempotent():
    sample = r"Before \(x\) and $$y$$"
    once = normalize_math(sample)
    assert normalize_math(once) == once


def test_clean_idempotent():
    sample = r"\centering \textbf{hello} \citep{k1, k2}"
    once = clean_latex_content(sample)
    assert clean_latex_content(once) == once


def test_preserve_macro_names_inside_cite_url_and_verbatim():
    preamble = r"\newcommand{\R}{\mathbb{R}}"
    body = (
        r"\cite{\R} \cite[see][p. 2]{\R} \citep*{\R} "
        r"\url{http://example.com/\R} \verb|\R| outside \R"
    )
    actual = extract_and_expand_macros(preamble, body)
    assert actual == (
        r"\cite{\R} \cite[see][p. 2]{\R} \citep*{\R} "
        r"\url{http://example.com/\R} \verb|\R| outside \mathbb{R}"
    )


def test_preserve_macro_names_inside_verbatim_environment():
    preamble = r"\newcommand{\R}{\mathbb{R}}"
    body = r"\begin{verbatim}\R\end{verbatim} then \R"
    assert extract_and_expand_macros(preamble, body) == (
        r"\begin{verbatim}\R\end{verbatim} then \mathbb{R}"
    )
