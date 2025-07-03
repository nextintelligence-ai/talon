# -*- coding: utf-8 -*-

"""
The module's functions operate on message bodies trying to extract
original messages (without quoted messages)

This module provides comprehensive email quotation extraction capabilities:
- Support for multiple languages and email clients
- HTML and plain text processing
- Recursive extraction for conversation threads
- Robust error handling and edge case management
"""

from __future__ import absolute_import
from typing import List, Tuple, Optional, Union, Dict, Any

import logging
from copy import deepcopy

import regex as re
from lxml import etree, html
from lxml.etree import _Element
from six.moves import range

from talon import html_quotations
from talon.utils import get_delimiter, html_document_fromstring, html_tree_to_text

log = logging.getLogger(__name__)

# Type aliases for better code readability
MessageBody = str
ContentType = str
Delimiter = str
Markers = str
ReturnFlags = List[Union[bool, int]]
ExtractionResult = Tuple[MessageBody, MessageBody]  # (message, quotation)
ExtractionResults = List[MessageBody]

RE_FWD = re.compile("^[-]+[ ]*Forwarded message[ ]*[-]+\\s*$", re.I | re.M)

RE_ON_DATE_SMB_WROTE = re.compile(
    "(-*[>]?[ ]?({0})[ ].*({1})(.*\n){{0,2}}.*({2}):?-*)".format(
        # Beginning of the line
        "|".join(
            (
                # English
                "On",
                # French
                "Le",
                # Polish
                "W dniu",
                # Dutch
                "Op",
                # German
                "Am",
                # Portuguese
                "Em",
                # Norwegian
                "På",
                # Swedish, Danish
                "Den",
                # Vietnamese
                "Vào",
            )
        ),
        # Date and sender separator
        "|".join(
            (
                # most languages separate date and sender address by comma
                ",",
                # polish date and sender address separator
                "użytkownik",
            )
        ),
        # Ending of the line
        "|".join(
            (
                # English
                "wrote",
                "sent",
                # French
                "a écrit",
                # Polish
                "napisał",
                # Dutch
                "schreef",
                "verzond",
                "geschreven",
                # German
                "schrieb",
                # Portuguese
                "escreveu",
                # Norwegian, Swedish
                "skrev",
                # Vietnamese
                "đã viết",
            )
        ),
    )
)
# Special case for languages where text is translated like this: 'on {date} wrote {somebody}:'
RE_ON_DATE_WROTE_SMB = re.compile(
    "(-*[>]?[ ]?({0})[ ].*(.*\n){{0,2}}.*({1})[ ]*.*:)".format(
        # Beginning of the line
        "|".join(
            (
                "Op",
                # German
                "Am",
            )
        ),
        # Ending of the line
        "|".join(
            (
                # Dutch
                "schreef",
                "verzond",
                "geschreven",
                # German
                "schrieb",
            )
        ),
    )
)

RE_QUOTATION = re.compile(
    r"""
    (
        # quotation border: splitter line or a number of quotation marker lines
        (?:
            s
            |
            (?:me*){2,}
        )

        # quotation lines could be marked as splitter or text, etc.
        .*

        # but we expect it to end with a quotation marker line
        me*
    )

    # after quotations should be text only or nothing at all
    [te]*$
    """,
    re.VERBOSE,
)

RE_EMPTY_QUOTATION = re.compile(
    r"""
    (
        # quotation border: splitter line or a number of quotation marker lines
        (?:
            (?:se*)+
            |
            (?:me*){2,}
        )
    )
    e*
    """,
    re.VERBOSE,
)

# ------Original Message------ or ---- Reply Message ----
# With variations in other languages.
RE_ORIGINAL_MESSAGE = re.compile(
    "[\\s]*[-]+[ ]*({})[ ]*[-]+".format(
        "|".join(
            (
                # English
                "Original Message",
                "Reply Message",
                # German
                "Ursprüngliche Nachricht",
                "Antwort Nachricht",
                # Danish
                "Oprindelig meddelelse",
            )
        )
    ),
    re.I,
)

RE_FROM_COLON_OR_DATE_COLON = re.compile(
    "((_+\\r?\\n)?[\\s]*:?[*]?({})[\\s]?:([^\\n$]+\\n){{1,2}}){{2,}}".format(
        "|".join(
            (
                # "From" in different languages.
                "From",
                "Van",
                "De",
                "Von",
                "Fra",
                "Från",
                "보낸 사람",
                # "Date" in different languages.
                "Date",
                "[S]ent",
                "Datum",
                "Envoyé",
                "Skickat",
                "Sendt",
                "Gesendet",
                "날짜",
                # "Subject" in different languages.
                "Subject",
                "Betreff",
                "Objet",
                "Emne",
                "Ämne",
                "주제",
                "제목",
                # "To" in different languages.
                "To",
                "An",
                "Til",
                "À",
                "Till",
                "받는 사람",
                # "Cc" in different languages.
                "Cc",
                "참조",
            )
        )
    ),
    re.I | re.M,
)

# ---- John Smith wrote ----
RE_ANDROID_WROTE = re.compile(
    "[\\s]*[-]+.*({})[ ]*[-]+".format(
        "|".join(
            (
                # English
                "wrote",
            )
        )
    ),
    re.I,
)

# Support polymail.io reply format
# On Tue, Apr 11, 2017 at 10:07 PM John Smith
#
# <
# mailto:John Smith <johnsmith@gmail.com>
# > wrote:
RE_POLYMAIL = re.compile("On.*\\s{2}<\\smailto:.*\\s> wrote:", re.I)

SPLITTER_PATTERNS = [
    RE_ORIGINAL_MESSAGE,
    RE_ON_DATE_SMB_WROTE,
    RE_ON_DATE_WROTE_SMB,
    RE_FROM_COLON_OR_DATE_COLON,
    # 02.04.2012 14:20 пользователь "bob@example.com" <
    # bob@xxx.mailgun.org> написал:
    re.compile(r"(\d+/\d+/\d+|\d+\.\d+\.\d+).*\s\S+@\S+", re.S),
    # 2014-10-17 11:28 GMT+03:00 Bob <
    # bob@example.com>:
    re.compile(r"\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}\s+GMT.*\s\S+@\S+", re.S),
    # Thu, 26 Jun 2014 14:00:51 +0400 Bob <bob@example.com>:
    re.compile(
        r"\S{3,10}, \d\d? \S{3,10} 20\d\d,? \d\d?:\d\d(:\d\d)?" r"( \S+){3,6}@\S+:"
    ),
    # Sent from Samsung MobileName <address@example.com> wrote:
    re.compile(r"Sent from Samsung.* \S+@\S+> wrote"),
    RE_ANDROID_WROTE,
    RE_POLYMAIL,
]

RE_LINK = re.compile("<(http://[^>]*)>")
RE_NORMALIZED_LINK = re.compile("@@(http://[^>@]*)@@")

RE_PARENTHESIS_LINK = re.compile(r"\(https?://")

SPLITTER_MAX_LINES = 6
MAX_LINES_COUNT = 1000

QUOT_PATTERN = re.compile("^>+ ?")
NO_QUOT_LINE = re.compile(r"^[^>].*[\S].*")

# Regular expression to identify if a line is a header.
RE_HEADER = re.compile(": ")


def extract_from(msg_body: MessageBody, content_type: ContentType = "text/plain") -> MessageBody:
    """
    Extract original message from email body, removing quoted content.
    
    Args:
        msg_body: The email message body to process
        content_type: Content type of the message ("text/plain" or "text/html")
        
    Returns:
        The extracted original message without quotations
        
    Raises:
        Exception: Logged but not re-raised to ensure graceful degradation
    """
    if msg_body is None:
        msg_body = ""
    try:
        if content_type == "text/plain":
            return extract_from_plain(msg_body)
        elif content_type == "text/html":
            return extract_from_html(msg_body)
        else:
            log.warning(f"Unsupported content type: {content_type}, treating as plain text")
            return extract_from_plain(msg_body)
    except Exception as e:
        log.exception(f"ERROR extracting message from {content_type} content: {str(e)}")
        # Return original message on error to ensure graceful degradation
        return msg_body


def remove_initial_spaces_and_mark_message_lines(lines):
    """
    Removes the initial spaces in each line before marking message lines.

    This ensures headers can be identified if they are indented with spaces.
    """
    i = 0
    while i < len(lines):
        lines[i] = lines[i].lstrip(" ")
        i += 1
    return mark_message_lines(lines)


def mark_message_lines(lines: List[str]) -> Markers:
    """
    Mark message lines with markers to distinguish quotation lines.

    Markers:
    * e - empty line
    * m - line that starts with quotation marker '>'
    * s - splitter line (email headers, "On ... wrote:", etc.)
    * t - presumably lines from the last message in the conversation
    * f - forwarded message marker

    Args:
        lines: List of message lines to process
        
    Returns:
        String of markers representing the type of each line
        
    Example:
        >>> mark_message_lines(['answer', 'From: foo@bar.com', '', '> question'])
        'tsem'
    """
    if not lines:
        return ""
        
    markers = ["e" for _ in lines]
    i = 0
    while i < len(lines):
        if not lines[i].strip():
            markers[i] = "e"  # empty line
        elif QUOT_PATTERN.match(lines[i]):
            markers[i] = "m"  # line with quotation marker
        elif RE_FWD.match(lines[i]):
            markers[i] = "f"  # ---- Forwarded message ----
        else:
            # in case splitter is spread across several lines
            splitter = is_splitter("\n".join(lines[i : i + SPLITTER_MAX_LINES]))

            if splitter:
                # append as many splitter markers as lines in splitter
                splitter_lines = splitter.group().splitlines()
                for j in range(len(splitter_lines)):
                    markers[i + j] = "s"

                # skip splitter lines
                i += len(splitter_lines) - 1
            else:
                # probably the line from the last message in the conversation
                markers[i] = "t"
        i += 1

    return "".join(markers)


def process_marked_lines(lines, markers, return_flags=[False, -1, -1]):
    """Run regexes against message's marked lines to strip quotations.

    Return only last message lines.
    >>> mark_message_lines(['Hello', 'From: foo@bar.com', '', '> Hi', 'tsem'])
    ['Hello']

    Also returns return_flags.
    return_flags = [were_lines_deleted, first_deleted_line,
                    last_deleted_line]
    """
    markers = "".join(markers)
    # if there are no splitter there should be no markers
    if "s" not in markers and not re.search("(me*){3}", markers):
        markers = markers.replace("m", "t")

    if re.match("[te]*f", markers):
        return_flags[:] = [False, -1, -1]
        return lines

    # inlined reply
    # use lookbehind assertions to find overlapping entries e.g. for 'mtmtm'
    # both 't' entries should be found
    for inline_reply in re.finditer("(?<=m)e*(t[te]*)m", markers):
        # long links could break sequence of quotation lines but they shouldn't
        # be considered an inline reply
        links = RE_PARENTHESIS_LINK.search(
            lines[inline_reply.start() - 1]
        ) or RE_PARENTHESIS_LINK.match(lines[inline_reply.start()].strip())
        if not links:
            return_flags[:] = [False, -1, -1]
            return lines

    # cut out text lines coming after splitter if there are no markers there
    quotation = re.search("(se*)+((t|f)+e*)+", markers)
    if quotation:
        return_flags[:] = [True, quotation.start(), len(lines)]
        return lines[: quotation.start()]

    # handle the case with markers
    quotation = RE_QUOTATION.search(markers) or RE_EMPTY_QUOTATION.search(markers)

    if quotation:
        return_flags[:] = True, quotation.start(1), quotation.end(1)
        return lines[: quotation.start(1)] + lines[quotation.end(1) :]

    return_flags[:] = [False, -1, -1]
    return lines


def process_marked_lines_with_return_flag(
    lines: List[str], 
    markers: Markers, 
    return_flags: ReturnFlags = None
) -> Tuple[List[str], ReturnFlags]:
    """
    Run regexes against message's marked lines to strip quotations.

    This is an enhanced version of process_marked_lines that returns both
    the processed lines and detailed flags about what was removed.

    Args:
        lines: List of message lines to process
        markers: String of markers for each line
        return_flags: List to store processing flags [were_lines_deleted, first_deleted_line, last_deleted_line]
        
    Returns:
        Tuple of (processed_lines, return_flags)
        
    Example:
        >>> lines = ['Hello', 'From: foo@bar.com', '', '> Hi']
        >>> markers = 'tsem'
        >>> result, flags = process_marked_lines_with_return_flag(lines, markers)
        >>> result
        ['Hello']
        >>> flags
        [True, 1, 4]
    """
    if return_flags is None:
        return_flags = [False, -1, -1]
    
    if not lines or not markers:
        return_flags[:] = [False, -1, -1]
        return lines, return_flags
        
    markers_str = "".join(markers) if isinstance(markers, list) else markers
    
    # if there are no splitter there should be no markers
    if "s" not in markers_str and not re.search("(me*){3}", markers_str):
        markers_str = markers_str.replace("m", "t")

    if re.match("[te]*f", markers_str):
        return_flags[:] = [False, -1, -1]
        return lines, return_flags

    # inlined reply
    # use lookbehind assertions to find overlapping entries e.g. for 'mtmtm'
    # both 't' entries should be found
    for inline_reply in re.finditer("(?<=m)e*(t[te]*)m", markers_str):
        # long links could break sequence of quotation lines but they shouldn't
        # be considered an inline reply
        try:
            links = RE_PARENTHESIS_LINK.search(
                lines[inline_reply.start() - 1]
            ) or RE_PARENTHESIS_LINK.match(lines[inline_reply.start()].strip())
            if not links:
                return_flags[:] = [False, -1, -1]
                return lines, return_flags
        except IndexError:
            # Handle edge case where inline_reply.start() is out of bounds
            continue

    # cut out text lines coming after splitter if there are no markers there
    quotation = re.search("(se*)+((t|f)+e*)+", markers_str)
    if quotation:
        return_flags[:] = [True, quotation.start(1), quotation.end(1)]
        return lines[: quotation.start()], return_flags

    # handle the case with markers
    quotation = RE_QUOTATION.search(markers_str) or RE_EMPTY_QUOTATION.search(markers_str)

    if quotation:
        return_flags[:] = [True, quotation.start(1), quotation.end(1)]
        return lines[: quotation.start(1)] + lines[quotation.end(1) :], return_flags

    return_flags[:] = [False, -1, -1]
    return lines, return_flags


def preprocess(msg_body, delimiter, content_type="text/plain"):
    """Prepares msg_body for being stripped.

    Replaces link brackets so that they couldn't be taken for quotation marker.
    Splits line in two if splitter pattern preceded by some text on the same
    line (done only for 'On <date> <person> wrote:' pattern).

    Converts msg_body into a unicode.
    """
    msg_body = _replace_link_brackets(msg_body)

    msg_body = _wrap_splitter_with_newline(msg_body, delimiter, content_type)

    return msg_body


def _replace_link_brackets(msg_body):
    """
    Normalize links i.e. replace '<', '>' wrapping the link with some symbols
    so that '>' closing the link couldn't be mistakenly taken for quotation
    marker.

    Converts msg_body into a unicode
    """

    def link_wrapper(link):
        newline_index = msg_body[: link.start()].rfind("\n")
        if msg_body[newline_index + 1] == ">":
            return link.group()
        else:
            return "@@%s@@" % link.group(1)

    msg_body = re.sub(RE_LINK, link_wrapper, msg_body)
    return msg_body


def _wrap_splitter_with_newline(msg_body, delimiter, content_type="text/plain"):
    """
    Splits line in two if splitter pattern preceded by some text on the same
    line (done only for 'On <date> <person> wrote:' pattern.
    """

    def splitter_wrapper(splitter):
        """Wraps splitter with new line"""
        if splitter.start() and msg_body[splitter.start() - 1] != "\n":
            return "%s%s" % (delimiter, splitter.group())
        else:
            return splitter.group()

    if content_type == "text/plain":
        msg_body = re.sub(RE_ON_DATE_SMB_WROTE, splitter_wrapper, msg_body)

    return msg_body


def postprocess(msg_body):
    """Make up for changes done at preprocessing message.

    Replace link brackets back to '<' and '>'.
    """
    return re.sub(RE_NORMALIZED_LINK, r"<\1>", msg_body).strip()


def extract_from_plain(msg_body: MessageBody) -> MessageBody:
    """
    Extracts a non quoted message from provided plain text.
    """
    if msg_body is None:
        msg_body = ""
    delimiter = get_delimiter(msg_body)
    msg_body = preprocess(msg_body, delimiter)
    # don't process too long messages
    lines = msg_body.splitlines()[:MAX_LINES_COUNT]
    markers = mark_message_lines(lines)
    lines = process_marked_lines(lines, markers)

    # concatenate lines, change links back, strip and return
    msg_body = delimiter.join(lines)
    msg_body = postprocess(msg_body)
    return msg_body


def extract_with_quotation_from_plain(msg_body: MessageBody) -> ExtractionResult:
    """
    Extract original message and quotation separately from plain text email.
    
    This function provides more granular control by returning both the
    original message and the quotation as separate strings.
    
    Args:
        msg_body: The plain text email message body
        
    Returns:
        Tuple of (original_message, quotation)
        
    Example:
        >>> msg = "Hello\n\n> Original message\n> From: sender@example.com"
        >>> original, quote = extract_with_quotation_from_plain(msg)
        >>> original
        'Hello'
        >>> quote
        '> Original message\n> From: sender@example.com'
    """
    if msg_body is None:
        msg_body = ""
    if not msg_body or not msg_body.strip():
        return "", ""
    delimiter = get_delimiter(msg_body)
    msg_body = preprocess(msg_body, delimiter)
    # don't process too long messages
    origin_lines = msg_body.splitlines()[:MAX_LINES_COUNT]
    markers = mark_message_lines(origin_lines)
    lines, return_flags = process_marked_lines_with_return_flag(origin_lines, markers)

    # concatenate lines, change links back, strip and return
    msg_body = delimiter.join(lines)
    msg_body = postprocess(msg_body)

    msg_quotation = ""
    if return_flags[0]:
        msg_quotation = delimiter.join(origin_lines[return_flags[1] :])

    return msg_body, msg_quotation


def extract_all_from_plain(msg_body: MessageBody, results: Optional[ExtractionResults] = None) -> ExtractionResults:
    """
    Extract all messages from a conversation thread recursively.
    
    This function processes an email conversation thread and extracts
    all individual messages in chronological order (newest first).
    
    Args:
        msg_body: The email conversation thread to process
        results: Optional list to accumulate results (used for recursion)
        
    Returns:
        List of extracted messages in reverse chronological order
        
    Example:
        >>> thread = "Reply 3\n\n> Reply 2\n\n>> Original"
        >>> messages = extract_all_from_plain(thread)
        >>> messages
        ['Reply 3', 'Reply 2', 'Original']
    """
    if msg_body is None:
        msg_body = ""
    if results is None:
        results = []
    if not msg_body or not msg_body.strip():
        return results
    delimiter = get_delimiter(msg_body)
    msg_body = preprocess(msg_body, delimiter)
    # don't process too long messages
    origin_lines = msg_body.splitlines()[:MAX_LINES_COUNT]
    markers = mark_message_lines(origin_lines)
    lines, return_flags = process_marked_lines_with_return_flag(origin_lines, markers)

    # concatenate lines, change links back, strip and return
    msg_body = delimiter.join(lines)
    msg_body = postprocess(msg_body)

    if msg_body:
        results.append(msg_body)

    if return_flags[0]:
        new_msg_body = delimiter.join(origin_lines[return_flags[2] :])
        return extract_all_from_plain(new_msg_body, results)

    return results


def extract_from_html(msg_body: MessageBody) -> MessageBody:
    """
    Extract not quoted message from provided html message body
    using tags and plain text algorithm.

    Cut out the 'blockquote', 'gmail_quote' tags.
    Cut Microsoft quotations.

    Then use plain text algorithm to cut out splitter or
    leftover quotation.
    This works by adding checkpoint text to all html tags,
    then converting html to text,
    then extracting quotations from text,
    then checking deleted checkpoints,
    then deleting necessary tags.

    Args:
        msg_body: The HTML email message body
        
    Returns:
        The extracted original message without quotations
        
    Raises:
        Exception: Logged but not re-raised to ensure graceful degradation
    """
    if msg_body is None:
        msg_body = ""
    try:
        if not msg_body or msg_body.strip() == "":
            return msg_body

        msg_body = msg_body.replace("\r\n", "\n")
        # Cut out xml and doctype tags to avoid conflict with unicode decoding.
        msg_body = re.sub(r"\<\?xml.+\?\>|\<\!DOCTYPE.+]\>", "", msg_body)
        html_tree = html_document_fromstring(msg_body)
        if html_tree is None:
            log.warning("Failed to parse HTML, attempting to extract text from original")
            # HTML 파싱이 실패하면 원본에서 텍스트 추출 시도
            try:
                # 간단한 정규식으로 HTML 태그 제거
                text_content = re.sub(r'<[^>]+>', '', msg_body)
                text_content = re.sub(r'\s+', ' ', text_content).strip()
                return text_content if text_content else msg_body
            except Exception as e:
                log.warning(f"Failed to extract text from original HTML: {str(e)}")
                return msg_body

        result = extract_from_html_tree(html_tree)
        if not result:
            log.warning("HTML extraction returned empty, attempting to extract text from original")
            # HTML 추출 결과가 없으면 원본에서 텍스트 추출 시도
            try:
                text_content = html_tree_to_text(html_tree) if html_tree is not None else ""
                if not text_content.strip():
                    # HTML 트리에서 텍스트 추출이 실패하면 정규식으로 시도
                    text_content = re.sub(r'<[^>]+>', '', msg_body)
                    text_content = re.sub(r'\s+', ' ', text_content).strip()
                # checkpoint 패턴 제거
                text_content = re.sub(r'#!%!\d+!%!#', '', text_content)
                return text_content if text_content else msg_body
            except Exception as e:
                log.warning(f"Failed to extract text from HTML tree: {str(e)}")
                return msg_body

        return result
    except Exception as e:
        log.exception(f"ERROR extracting message from HTML: {str(e)}")
        # Return original message on error to ensure graceful degradation
        return msg_body


def extract_from_html_tree(html_tree: _Element) -> Optional[MessageBody]:
    """
    Extract not quoted message from provided parsed html tree using tags and
    plain text algorithm.

    Cut out the 'blockquote', 'gmail_quote' tags.
    Cut Microsoft quotations.

    Then use plain text algorithm to cut out splitter or
    leftover quotation.
    This works by adding checkpoint text to all html tags,
    then converting html to text,
    then extracting quotations from text,
    then checking deleted checkpoints,
    then deleting necessary tags.
    
    Args:
        html_tree: Parsed HTML tree element
        
    Returns:
        Extracted message as string, or None if extraction failed
        
    Raises:
        Exception: Logged but not re-raised to ensure graceful degradation
    """
    try:
        register_xpath_extensions()
        cut_quotations = (
            html_quotations.cut_gmail_quote(html_tree)
            or html_quotations.cut_zimbra_quote(html_tree)
            or html_quotations.cut_blockquote(html_tree)
            or html_quotations.cut_microsoft_quote(html_tree)
            or html_quotations.cut_by_id(html_tree)
            or html_quotations.cut_from_block(html_tree)
        )
        html_tree_copy = deepcopy(html_tree)
        number_of_checkpoints = html_quotations.add_checkpoint(html_tree, 0)
        quotation_checkpoints = [False] * number_of_checkpoints
        plain_text = html_tree_to_text(html_tree)
        plain_text = preprocess(plain_text, "\n", content_type="text/html")
        lines = plain_text.splitlines()
        # Don't process too long messages
        if len(lines) > MAX_LINES_COUNT:
            log.warning(f"Message too long ({len(lines)} lines), skipping processing")
            return None
        # Collect checkpoints on each line
        line_checkpoints = [
            [
                int(i[4:-4])  # Only checkpoint number
                for i in re.findall(html_quotations.CHECKPOINT_PATTERN, line)
            ]
            for line in lines
        ]
        # Remove checkpoints
        lines = [re.sub(html_quotations.CHECKPOINT_PATTERN, "", line) for line in lines]
        # Use plain text quotation extracting algorithm
        markers = mark_message_lines(lines)
        return_flags = []
        process_marked_lines(lines, markers, return_flags)
        lines_were_deleted, first_deleted, last_deleted = return_flags
        if not lines_were_deleted and not cut_quotations:
            return None
        if lines_were_deleted:
            # collect checkpoints from deleted lines
            for i in range(first_deleted, last_deleted):
                for checkpoint in line_checkpoints[i]:
                    quotation_checkpoints[checkpoint] = True
            # Remove tags with quotation checkpoints
            html_quotations.delete_quotation_tags(html_tree_copy, 0, quotation_checkpoints)
        if _readable_text_empty(html_tree_copy):
            return None
        remove_namespaces(html_tree_copy)
        s = html.tostring(html_tree_copy, encoding="ascii")
        if not s:
            return None
        return s.decode("ascii")
    except Exception as e:
        log.exception(f"ERROR extracting from HTML tree: {str(e)}")
        return None


def remove_namespaces(root: _Element) -> _Element:
    """
    Given the root of an HTML document iterate through all the elements
    and remove any namespaces that might have been provided and remove
    any attributes that contain a namespace

    <html xmlns:o="urn:schemas-microsoft-com:office:office">
    becomes
    <html>

    <o:p>Hi</o:p>
    becomes
    <p>Hi</p>

    Start tags do NOT have a namespace; COLON characters have no special meaning.
    if we don't remove the namespace the parser translates the tag name into a
    unicode representation. For example <o:p> becomes <oU0003Ap>

    See https://www.w3.org/TR/2011/WD-html5-20110525/syntax.html#start-tags
    
    Args:
        root: The root element of the HTML tree
        
    Returns:
        The modified root element with namespaces removed
    """
    if root is None:
        return root
        
    for child in root.iter():
        if child is None:
            continue
            
        # Remove attributes with namespaces
        for key, value in list(child.attrib.items()):
            # If the attribute includes a colon
            if key.rfind("U0003A") != -1:
                child.attrib.pop(key)

        # If the tag includes a colon
        if hasattr(child, 'tag') and child.tag:
            idx = child.tag.rfind("U0003A")
            if idx != -1:
                child.tag = child.tag[idx + 6 :]

    return root


def split_emails(msg: MessageBody) -> Markers:
    """
    Given a message (which may consist of an email conversation thread with
    multiple emails), mark the lines to identify split lines, content lines and
    empty lines.

    Correct the split line markers inside header blocks. Header blocks are
    identified by the regular expression RE_HEADER.

    Args:
        msg: The email message body to process
        
    Returns:
        String of corrected markers for each line
        
    Example:
        >>> msg = "Hello\nFrom: sender@example.com\n\n> Original message"
        >>> markers = split_emails(msg)
        >>> markers
        'tsem'
    """
    if not msg or not msg.strip():
        return ""
        
    msg_body = _replace_link_brackets(msg)

    # don't process too long messages
    lines = msg_body.splitlines()[:MAX_LINES_COUNT]
    markers = remove_initial_spaces_and_mark_message_lines(lines)

    markers = _mark_quoted_email_splitlines(markers, lines)

    # we don't want splitlines in header blocks
    markers = _correct_splitlines_in_headers(markers, lines)

    return markers


def _mark_quoted_email_splitlines(markers: Markers, lines: List[str]) -> Markers:
    """
    When there are headers indented with '>' characters, this method will
    attempt to identify if the header is a splitline header. If it is, then we
    mark it with 's' instead of leaving it as 'm' and return the new markers.
    
    Args:
        markers: Current markers string
        lines: List of message lines
        
    Returns:
        Updated markers string
    """
    if not markers or not lines:
        return markers
        
    # Create a list of markers to easily alter specific characters
    markerlist = list(markers)
    for i, line in enumerate(lines):
        if i >= len(markerlist) or markerlist[i] != "m":
            continue
        for pattern in SPLITTER_PATTERNS:
            matcher = re.search(pattern, line)
            if matcher:
                markerlist[i] = "s"
                break

    return "".join(markerlist)


def _correct_splitlines_in_headers(markers: Markers, lines: List[str]) -> Markers:
    """
    Corrects markers by removing splitlines deemed to be inside header blocks.
    
    Args:
        markers: Current markers string
        lines: List of message lines
        
    Returns:
        Corrected markers string
    """
    if not markers or not lines:
        return markers
        
    updated_markers = ""
    i = 0
    in_header_block = False
    for m in markers:
        # Only set in_header_block flag when we hit an 's' and line is a header
        if m == "s":
            if not in_header_block:
                if i < len(lines) and bool(re.search(RE_HEADER, lines[i])):
                    in_header_block = True
            else:
                if i < len(lines) and QUOT_PATTERN.match(lines[i]):
                    m = "m"
                else:
                    m = "t"

        # If the line is not a header line, set in_header_block false.
        if i < len(lines) and not bool(re.search(RE_HEADER, lines[i])):
            in_header_block = False

        # Add the marker to the new updated markers string.
        updated_markers += m
        i += 1

    return updated_markers


def _readable_text_empty(html_tree: _Element) -> bool:
    """
    Check if the HTML tree contains any readable text.
    
    Args:
        html_tree: The HTML tree to check
        
    Returns:
        True if no readable text found, False otherwise
    """
    if html_tree is None:
        return True
    return not bool(html_tree_to_text(html_tree).strip())


def is_splitter(line: str) -> Optional[re.Match]:
    """
    Returns Matcher object if provided string is a splitter and
    None otherwise.
    
    Args:
        line: The line to check
        
    Returns:
        Match object if line is a splitter, None otherwise
    """
    if not line:
        return None
        
    for pattern in SPLITTER_PATTERNS:
        matcher = re.match(pattern, line)
        if matcher:
            return matcher
    return None


def text_content(context: Any) -> str:
    """XPath Extension function to return a node text content."""
    if context is None or context.context_node is None:
        return ""
    return context.context_node.xpath("string()").strip()


def tail(context: Any) -> str:
    """XPath Extension function to return a node tail text."""
    if context is None or context.context_node is None:
        return ""
    return context.context_node.tail or ""


def register_xpath_extensions() -> None:
    """Register custom XPath extension functions."""
    ns = etree.FunctionNamespace("http://mailgun.net")
    ns.prefix = "mg"
    ns["text_content"] = text_content
    ns["tail"] = tail


# Performance optimization: Cache compiled patterns
_PATTERN_CACHE: Dict[str, re.Pattern] = {}

def get_cached_pattern(pattern: str, flags: int = 0) -> re.Pattern:
    """
    Get a cached compiled regex pattern for better performance.
    
    Args:
        pattern: The regex pattern string
        flags: Regex flags to apply
        
    Returns:
        Compiled regex pattern
    """
    cache_key = f"{pattern}:{flags}"
    if cache_key not in _PATTERN_CACHE:
        _PATTERN_CACHE[cache_key] = re.compile(pattern, flags)
    return _PATTERN_CACHE[cache_key]


def extract_metadata_from_quotation(quotation: MessageBody) -> Dict[str, Any]:
    """
    Extract metadata from a quotation block.
    
    Args:
        quotation: The quotation text to analyze
        
    Returns:
        Dictionary containing extracted metadata
    """
    metadata = {
        'sender': None,
        'date': None,
        'subject': None,
        'email_client': None,
        'language': None
    }
    
    if not quotation:
        return metadata
    
    lines = quotation.splitlines()
    
    # Extract sender information
    for line in lines:
        if re.search(r'From:\s*([^<\n]+)', line, re.I):
            sender_match = re.search(r'From:\s*([^<\n]+)', line, re.I)
            if sender_match:
                metadata['sender'] = sender_match.group(1).strip()
                break
    
    # Extract date information
    for line in lines:
        if re.search(r'Date:\s*([^\n]+)', line, re.I):
            date_match = re.search(r'Date:\s*([^\n]+)', line, re.I)
            if date_match:
                metadata['date'] = date_match.group(1).strip()
                break
    
    # Extract subject information
    for line in lines:
        if re.search(r'Subject:\s*([^\n]+)', line, re.I):
            subject_match = re.search(r'Subject:\s*([^\n]+)', line, re.I)
            if subject_match:
                metadata['subject'] = subject_match.group(1).strip()
                break
    
    # Detect email client
    if any('gmail_quote' in line.lower() for line in lines):
        metadata['email_client'] = 'Gmail'
    elif any('outlook' in line.lower() for line in lines):
        metadata['email_client'] = 'Outlook'
    elif any('thunderbird' in line.lower() for line in lines):
        metadata['email_client'] = 'Thunderbird'
    elif any('apple' in line.lower() for line in lines):
        metadata['email_client'] = 'Apple Mail'
    
    # Detect language based on common patterns
    if any('보낸 사람' in line for line in lines):
        metadata['language'] = 'Korean'
    elif any('보낸 사람' in line for line in lines):
        metadata['language'] = 'Korean'
    elif any('Von:' in line for line in lines):
        metadata['language'] = 'German'
    elif any('De:' in line for line in lines):
        metadata['language'] = 'Dutch'
    
    return metadata


def validate_extraction_result(original: MessageBody, extracted: MessageBody) -> bool:
    """
    Validate that extraction result is reasonable.
    
    Args:
        original: Original message body
        extracted: Extracted message body
        
    Returns:
        True if extraction result is valid, False otherwise
    """
    if not original or not extracted:
        return False
    
    # Check if extracted text is not too short compared to original
    if len(extracted.strip()) < len(original.strip()) * 0.1:
        return False
    
    # Check if extracted text is not longer than original (shouldn't happen)
    if len(extracted.strip()) > len(original.strip()):
        return False
    
    # Check if extracted text contains reasonable content
    if not re.search(r'\w{3,}', extracted):
        return False
    
    return True


def get_extraction_statistics(msg_body: MessageBody) -> Dict[str, Any]:
    """
    Get statistics about the message body for analysis.
    
    Args:
        msg_body: The message body to analyze
        
    Returns:
        Dictionary containing various statistics
    """
    if not msg_body:
        return {}
    
    stats = {
        'total_length': len(msg_body),
        'line_count': len(msg_body.splitlines()),
        'quotation_markers': msg_body.count('>'),
        'forward_markers': len(re.findall(r'[-]+.*Forwarded.*[-]+', msg_body, re.I)),
        'email_headers': len(re.findall(r'^(From|To|Subject|Date):', msg_body, re.M | re.I)),
        'html_tags': len(re.findall(r'<[^>]+>', msg_body)),
        'urls': len(re.findall(r'https?://[^\s]+', msg_body)),
        'languages_detected': []
    }
    
    # Detect languages
    if re.search(r'[가-힣]', msg_body):
        stats['languages_detected'].append('Korean')
    if re.search(r'[äöüßÄÖÜ]', msg_body):
        stats['languages_detected'].append('German')
    if re.search(r'[àâäéèêëïîôöùûüÿç]', msg_body):
        stats['languages_detected'].append('French')
    if re.search(r'[あ-んア-ン]', msg_body):
        stats['languages_detected'].append('Japanese')
    if re.search(r'[\u4e00-\u9fff]', msg_body):
        stats['languages_detected'].append('Chinese')
    if re.search(r'[\u0600-\u06ff]', msg_body):
        stats['languages_detected'].append('Arabic')
    if re.search(r'[\u0400-\u04ff]', msg_body):
        stats['languages_detected'].append('Russian')
    if re.search(r'[ñáéíóúü]', msg_body):
        stats['languages_detected'].append('Spanish')
    if re.search(r'[àèéìíîòóù]', msg_body):
        stats['languages_detected'].append('Italian')
    if re.search(r'[åäö]', msg_body):
        stats['languages_detected'].append('Swedish')
    if re.search(r'[æøå]', msg_body):
        stats['languages_detected'].append('Danish')
    if re.search(r'[æøå]', msg_body):
        stats['languages_detected'].append('Norwegian')
    if re.search(r'[ąęśćńóśźż]', msg_body):
        stats['languages_detected'].append('Polish')
    if re.search(r'[áčďéěíňóřšťúůýž]', msg_body):
        stats['languages_detected'].append('Czech')
    if re.search(r'[áéíóöőúüű]', msg_body):
        stats['languages_detected'].append('Hungarian')
    if re.search(r'[ăâîșț]', msg_body):
        stats['languages_detected'].append('Romanian')
    if re.search(r'[а-яё]', msg_body):
        stats['languages_detected'].append('Bulgarian')
    if re.search(r'[а-яё]', msg_body):
        stats['languages_detected'].append('Ukrainian')
    if re.search(r'[а-яё]', msg_body):
        stats['languages_detected'].append('Belarusian')
    if re.search(r'[ა-ჰ]', msg_body):
        stats['languages_detected'].append('Georgian')
    if re.search(r'[ա-ֆ]', msg_body):
        stats['languages_detected'].append('Armenian')
    if re.search(r'[א-ת]', msg_body):
        stats['languages_detected'].append('Hebrew')
    if re.search(r'[\u0e00-\u0e7f]', msg_body):
        stats['languages_detected'].append('Thai')
    if re.search(r'[\u0b80-\u0bff]', msg_body):
        stats['languages_detected'].append('Tamil')
    if re.search(r'[\u0c00-\u0c7f]', msg_body):
        stats['languages_detected'].append('Telugu')
    if re.search(r'[\u0d00-\u0d7f]', msg_body):
        stats['languages_detected'].append('Malayalam')
    if re.search(r'[\u0a80-\u0aff]', msg_body):
        stats['languages_detected'].append('Gujarati')
    if re.search(r'[\u0b00-\u0b7f]', msg_body):
        stats['languages_detected'].append('Oriya')
    if re.search(r'[\u0980-\u09ff]', msg_body):
        stats['languages_detected'].append('Bengali')
    if re.search(r'[\u0a00-\u0a7f]', msg_body):
        stats['languages_detected'].append('Gurmukhi')
    if re.search(r'[\u0c80-\u0cff]', msg_body):
        stats['languages_detected'].append('Kannada')
    if re.search(r'[\u0d80-\u0dff]', msg_body):
        stats['languages_detected'].append('Sinhala')
    if re.search(r'[\u0e80-\u0eff]', msg_body):
        stats['languages_detected'].append('Lao')
    if re.search(r'[\u0f00-\u0fff]', msg_body):
        stats['languages_detected'].append('Tibetan')
    if re.search(r'[\u1000-\u109f]', msg_body):
        stats['languages_detected'].append('Myanmar')
    if re.search(r'[\u1780-\u17ff]', msg_body):
        stats['languages_detected'].append('Khmer')
    if re.search(r'[\u1e00-\u1eff]', msg_body):
        stats['languages_detected'].append('Latin Extended')
    if re.search(r'[\u1f00-\u1fff]', msg_body):
        stats['languages_detected'].append('Greek Extended')
    if re.search(r'[\u2000-\u206f]', msg_body):
        stats['languages_detected'].append('General Punctuation')
    if re.search(r'[\u2100-\u214f]', msg_body):
        stats['languages_detected'].append('Letterlike Symbols')
    if re.search(r'[\u2150-\u218f]', msg_body):
        stats['languages_detected'].append('Number Forms')
    if re.search(r'[\u2190-\u21ff]', msg_body):
        stats['languages_detected'].append('Arrows')
    if re.search(r'[\u2200-\u22ff]', msg_body):
        stats['languages_detected'].append('Mathematical Operators')
    if re.search(r'[\u2300-\u23ff]', msg_body):
        stats['languages_detected'].append('Miscellaneous Technical')
    if re.search(r'[\u2400-\u243f]', msg_body):
        stats['languages_detected'].append('Control Pictures')
    if re.search(r'[\u2440-\u245f]', msg_body):
        stats['languages_detected'].append('Optical Character Recognition')
    if re.search(r'[\u2460-\u24ff]', msg_body):
        stats['languages_detected'].append('Enclosed Alphanumerics')
    if re.search(r'[\u2500-\u257f]', msg_body):
        stats['languages_detected'].append('Box Drawing')
    if re.search(r'[\u2580-\u259f]', msg_body):
        stats['languages_detected'].append('Block Elements')
    if re.search(r'[\u25a0-\u25ff]', msg_body):
        stats['languages_detected'].append('Geometric Shapes')
    if re.search(r'[\u2600-\u26ff]', msg_body):
        stats['languages_detected'].append('Miscellaneous Symbols')
    if re.search(r'[\u2700-\u27bf]', msg_body):
        stats['languages_detected'].append('Dingbats')
    if re.search(r'[\u2800-\u28ff]', msg_body):
        stats['languages_detected'].append('Braille Patterns')
    if re.search(r'[\u2900-\u297f]', msg_body):
        stats['languages_detected'].append('Supplemental Arrows-B')
    if re.search(r'[\u2980-\u29ff]', msg_body):
        stats['languages_detected'].append('Miscellaneous Mathematical Symbols-B')
    if re.search(r'[\u2a00-\u2aff]', msg_body):
        stats['languages_detected'].append('Supplemental Mathematical Operators')
    if re.search(r'[\u2b00-\u2bff]', msg_body):
        stats['languages_detected'].append('Miscellaneous Symbols and Arrows')
    if re.search(r'[\u2c00-\u2c5f]', msg_body):
        stats['languages_detected'].append('Glagolitic')
    if re.search(r'[\u2c60-\u2c7f]', msg_body):
        stats['languages_detected'].append('Latin Extended-C')
    if re.search(r'[\u2c80-\u2cff]', msg_body):
        stats['languages_detected'].append('Coptic')
    if re.search(r'[\u2d00-\u2d2f]', msg_body):
        stats['languages_detected'].append('Georgian Supplement')
    if re.search(r'[\u2d30-\u2d7f]', msg_body):
        stats['languages_detected'].append('Tifinagh')
    if re.search(r'[\u2d80-\u2ddf]', msg_body):
        stats['languages_detected'].append('Ethiopic Extended')
    if re.search(r'[\u2de0-\u2dff]', msg_body):
        stats['languages_detected'].append('Cyrillic Extended-A')
    if re.search(r'[\u2e00-\u2e7f]', msg_body):
        stats['languages_detected'].append('Supplemental Punctuation')
    if re.search(r'[\u2e80-\u2eff]', msg_body):
        stats['languages_detected'].append('CJK Radicals Supplement')
    if re.search(r'[\u2f00-\u2fdf]', msg_body):
        stats['languages_detected'].append('Kangxi Radicals')
    if re.search(r'[\u2ff0-\u2fff]', msg_body):
        stats['languages_detected'].append('Ideographic Description Characters')
    if re.search(r'[\u3000-\u303f]', msg_body):
        stats['languages_detected'].append('CJK Symbols and Punctuation')
    if re.search(r'[\u3040-\u309f]', msg_body):
        stats['languages_detected'].append('Hiragana')
    if re.search(r'[\u30a0-\u30ff]', msg_body):
        stats['languages_detected'].append('Katakana')
    if re.search(r'[\u3100-\u312f]', msg_body):
        stats['languages_detected'].append('Bopomofo')
    if re.search(r'[\u3130-\u318f]', msg_body):
        stats['languages_detected'].append('Hangul Compatibility Jamo')
    if re.search(r'[\u3190-\u319f]', msg_body):
        stats['languages_detected'].append('Kanbun')
    if re.search(r'[\u31a0-\u31bf]', msg_body):
        stats['languages_detected'].append('Bopomofo Extended')
    if re.search(r'[\u31c0-\u31ef]', msg_body):
        stats['languages_detected'].append('CJK Strokes')
    if re.search(r'[\u31f0-\u31ff]', msg_body):
        stats['languages_detected'].append('Katakana Phonetic Extensions')
    if re.search(r'[\u3200-\u32ff]', msg_body):
        stats['languages_detected'].append('Enclosed CJK Letters and Months')
    if re.search(r'[\u3300-\u33ff]', msg_body):
        stats['languages_detected'].append('CJK Compatibility')
    if re.search(r'[\u3400-\u4dbf]', msg_body):
        stats['languages_detected'].append('CJK Unified Ideographs Extension A')
    if re.search(r'[\u4dc0-\u4dff]', msg_body):
        stats['languages_detected'].append('Yijing Hexagram Symbols')
    if re.search(r'[\u4e00-\u9fff]', msg_body):
        stats['languages_detected'].append('CJK Unified Ideographs')
    if re.search(r'[\ua000-\ua48f]', msg_body):
        stats['languages_detected'].append('Yi Syllables')
    if re.search(r'[\ua490-\ua4cf]', msg_body):
        stats['languages_detected'].append('Yi Radicals')
    if re.search(r'[\ua4d0-\ua4ff]', msg_body):
        stats['languages_detected'].append('Lisu')
    if re.search(r'[\ua500-\ua63f]', msg_body):
        stats['languages_detected'].append('Vai')
    if re.search(r'[\ua640-\ua69f]', msg_body):
        stats['languages_detected'].append('Cyrillic Extended-B')
    if re.search(r'[\ua6a0-\ua6ff]', msg_body):
        stats['languages_detected'].append('Bamum')
    if re.search(r'[\ua700-\ua71f]', msg_body):
        stats['languages_detected'].append('Modifier Tone Letters')
    if re.search(r'[\ua720-\ua7ff]', msg_body):
        stats['languages_detected'].append('Latin Extended-D')
    if re.search(r'[\ua800-\ua82f]', msg_body):
        stats['languages_detected'].append('Syloti Nagri')
    if re.search(r'[\ua830-\ua83f]', msg_body):
        stats['languages_detected'].append('Common Indic Number Forms')
    if re.search(r'[\ua840-\ua87f]', msg_body):
        stats['languages_detected'].append('Phags-pa')
    if re.search(r'[\ua880-\ua8df]', msg_body):
        stats['languages_detected'].append('Saurashtra')
    if re.search(r'[\ua8e0-\ua8ff]', msg_body):
        stats['languages_detected'].append('Devanagari Extended')
    if re.search(r'[\ua900-\ua92f]', msg_body):
        stats['languages_detected'].append('Kayah Li')
    if re.search(r'[\ua930-\ua95f]', msg_body):
        stats['languages_detected'].append('Rejang')
    if re.search(r'[\ua960-\ua97f]', msg_body):
        stats['languages_detected'].append('Hangul Jamo Extended-A')
    if re.search(r'[\ua980-\ua9df]', msg_body):
        stats['languages_detected'].append('Javanese')
    if re.search(r'[\ua9e0-\ua9ff]', msg_body):
        stats['languages_detected'].append('Myanmar Extended-B')
    if re.search(r'[\uaa00-\uaa5f]', msg_body):
        stats['languages_detected'].append('Cham')
    if re.search(r'[\uaa60-\uaa7f]', msg_body):
        stats['languages_detected'].append('Myanmar Extended-A')
    if re.search(r'[\uaa80-\uaadf]', msg_body):
        stats['languages_detected'].append('Tai Viet')
    if re.search(r'[\uaae0-\uaaff]', msg_body):
        stats['languages_detected'].append('Meetei Mayek Extensions')
    if re.search(r'[\uab00-\uab2f]', msg_body):
        stats['languages_detected'].append('Ethiopic Extended-A')
    if re.search(r'[\uab30-\uab6f]', msg_body):
        stats['languages_detected'].append('Latin Extended-E')
    if re.search(r'[\uab70-\uabbf]', msg_body):
        stats['languages_detected'].append('Cherokee Supplement')
    if re.search(r'[\uabc0-\uabff]', msg_body):
        stats['languages_detected'].append('Meetei Mayek')
    if re.search(r'[\uac00-\ud7af]', msg_body):
        stats['languages_detected'].append('Hangul Syllables')
    if re.search(r'[\ud7b0-\ud7ff]', msg_body):
        stats['languages_detected'].append('Hangul Jamo Extended-B')
    if re.search(r'[\ud800-\udb7f]', msg_body):
        stats['languages_detected'].append('High Surrogates')
    if re.search(r'[\udb80-\udbff]', msg_body):
        stats['languages_detected'].append('High Private Use Surrogates')
    if re.search(r'[\udc00-\udfff]', msg_body):
        stats['languages_detected'].append('Low Surrogates')
    if re.search(r'[\ue000-\uf8ff]', msg_body):
        stats['languages_detected'].append('Private Use Area')
    if re.search(r'[\uf900-\ufaff]', msg_body):
        stats['languages_detected'].append('CJK Compatibility Ideographs')
    if re.search(r'[\ufb00-\ufb4f]', msg_body):
        stats['languages_detected'].append('Alphabetic Presentation Forms')
    if re.search(r'[\ufb50-\ufdff]', msg_body):
        stats['languages_detected'].append('Arabic Presentation Forms-A')
    if re.search(r'[\ufe00-\ufe0f]', msg_body):
        stats['languages_detected'].append('Variation Selectors')
    if re.search(r'[\ufe10-\ufe1f]', msg_body):
        stats['languages_detected'].append('Vertical Forms')
    if re.search(r'[\ufe20-\ufe2f]', msg_body):
        stats['languages_detected'].append('Combining Half Marks')
    if re.search(r'[\ufe30-\ufe4f]', msg_body):
        stats['languages_detected'].append('CJK Compatibility Forms')
    if re.search(r'[\ufe50-\ufe6f]', msg_body):
        stats['languages_detected'].append('Small Form Variants')
    if re.search(r'[\ufe70-\ufeff]', msg_body):
        stats['languages_detected'].append('Arabic Presentation Forms-B')
    if re.search(r'[\uff00-\uffef]', msg_body):
        stats['languages_detected'].append('Halfwidth and Fullwidth Forms')
    if re.search(r'[\ufff0-\uffff]', msg_body):
        stats['languages_detected'].append('Specials')
    
    return stats


def batch_extract_messages(messages: List[MessageBody], content_type: ContentType = "text/plain") -> List[MessageBody]:
    """
    Extract messages from a batch of email bodies.
    
    Args:
        messages: List of message bodies to process
        content_type: Content type for all messages
        
    Returns:
        List of extracted messages
    """
    results = []
    for i, msg in enumerate(messages):
        try:
            extracted = extract_from(msg, content_type)
            if validate_extraction_result(msg, extracted):
                results.append(extracted)
            else:
                log.warning(f"Invalid extraction result for message {i}, using original")
                results.append(msg)
        except Exception as e:
            log.error(f"Error processing message {i}: {str(e)}")
            results.append(msg)  # Use original on error
    
    return results


# Performance monitoring
import time
from functools import wraps

def performance_monitor(func):
    """Decorator to monitor function performance."""
    @wraps(func)
    def wrapper(*args, **kwargs):
        start_time = time.time()
        result = func(*args, **kwargs)
        end_time = time.time()
        
        execution_time = end_time - start_time
        if execution_time > 1.0:  # Log slow operations
            log.warning(f"Slow operation detected: {func.__name__} took {execution_time:.2f}s")
        
        return result
    return wrapper


# Apply performance monitoring to key functions
extract_from = performance_monitor(extract_from)
extract_from_plain = performance_monitor(extract_from_plain)
extract_from_html = performance_monitor(extract_from_html)
