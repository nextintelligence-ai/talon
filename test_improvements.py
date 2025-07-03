#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Test script for improved talon quotations module
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from talon.quotations import (
    extract_from,
    extract_with_quotation_from_plain,
    extract_all_from_plain,
    extract_metadata_from_quotation,
    validate_extraction_result,
    get_extraction_statistics,
    batch_extract_messages,
    get_cached_pattern
)


def test_basic_extraction():
    """Test basic message extraction"""
    print("=== Testing Basic Extraction ===")
    
    # Simple case
    msg = "Hello world!\n\n> Original message\n> From: sender@example.com"
    result = extract_from(msg)
    print(f"Original: {msg}")
    print(f"Extracted: {result}")
    print(f"Valid: {validate_extraction_result(msg, result)}")
    print()


def test_quotation_extraction():
    """Test extraction with quotation separation"""
    print("=== Testing Quotation Extraction ===")
    
    msg = "Hello world!\n\n> Original message\n> From: sender@example.com"
    original, quotation = extract_with_quotation_from_plain(msg)
    print(f"Original message: {original}")
    print(f"Quotation: {quotation}")
    print()


def test_conversation_thread():
    """Test conversation thread extraction"""
    print("=== Testing Conversation Thread ===")
    
    thread = """Reply 3

> Reply 2
> From: user2@example.com
> 
> > Original message
> > From: user1@example.com"""
    
    messages = extract_all_from_plain(thread)
    print(f"Found {len(messages)} messages:")
    for i, msg in enumerate(messages, 1):
        print(f"Message {i}: {msg[:50]}...")
    print()


def test_metadata_extraction():
    """Test metadata extraction from quotations"""
    print("=== Testing Metadata Extraction ===")
    
    quotation = """From: John Doe <john@example.com>
Date: Mon, 1 Jan 2024 12:00:00 +0000
Subject: Test Message

This is the original message."""
    
    metadata = extract_metadata_from_quotation(quotation)
    print(f"Extracted metadata: {metadata}")
    print()


def test_multilingual_support():
    """Test multilingual support"""
    print("=== Testing Multilingual Support ===")
    
    # Korean
    korean_msg = """안녕하세요!

보낸 사람: 김철수 <kim@example.com>
날짜: 2024년 1월 1일
제목: 테스트 메시지

> 원본 메시지입니다."""
    
    result = extract_from(korean_msg)
    stats = get_extraction_statistics(korean_msg)
    print(f"Korean message extracted: {result}")
    print(f"Korean statistics: {stats}")
    
    # German
    german_msg = """Hallo!

Von: Hans Mueller <hans@example.com>
Datum: 1. Januar 2024
Betreff: Test Nachricht

> Ursprüngliche Nachricht."""
    
    result = extract_from(german_msg)
    stats = get_extraction_statistics(german_msg)
    print(f"German message extracted: {result}")
    print(f"German statistics: {stats}")
    
    # French
    french_msg = """Bonjour!

De: Jean Dupont <jean@example.com>
Date: 1er janvier 2024
Objet: Message de test

> Message original."""
    
    result = extract_from(french_msg)
    stats = get_extraction_statistics(french_msg)
    print(f"French message extracted: {result}")
    print(f"French statistics: {stats}")
    
    # Japanese
    japanese_msg = """こんにちは！

送信者: 田中太郎 <tanaka@example.com>
日付: 2024年1月1日
件名: テストメッセージ

> 元のメッセージです。"""
    
    result = extract_from(japanese_msg)
    stats = get_extraction_statistics(japanese_msg)
    print(f"Japanese message extracted: {result}")
    print(f"Japanese statistics: {stats}")
    
    # Chinese
    chinese_msg = """你好！

发件人: 张三 <zhang@example.com>
日期: 2024年1月1日
主题: 测试消息

> 原始消息。"""
    
    result = extract_from(chinese_msg)
    stats = get_extraction_statistics(chinese_msg)
    print(f"Chinese message extracted: {result}")
    print(f"Chinese statistics: {stats}")
    
    # Arabic
    arabic_msg = """مرحبا!

من: أحمد محمد <ahmed@example.com>
التاريخ: 1 يناير 2024
الموضوع: رسالة اختبار

> الرسالة الأصلية."""
    
    result = extract_from(arabic_msg)
    stats = get_extraction_statistics(arabic_msg)
    print(f"Arabic message extracted: {result}")
    print(f"Arabic statistics: {stats}")
    
    # Russian
    russian_msg = """Привет!

От: Иван Петров <ivan@example.com>
Дата: 1 января 2024
Тема: Тестовое сообщение

> Оригинальное сообщение."""
    
    result = extract_from(russian_msg)
    stats = get_extraction_statistics(russian_msg)
    print(f"Russian message extracted: {result}")
    print(f"Russian statistics: {stats}")
    
    # Mixed language test
    mixed_msg = """Hello! 안녕하세요! Bonjour!

From: John Doe <john@example.com>
보낸 사람: 김철수 <kim@example.com>
De: Jean Dupont <jean@example.com>

> Original message
> 원본 메시지
> Message original"""
    
    result = extract_from(mixed_msg)
    stats = get_extraction_statistics(mixed_msg)
    print(f"Mixed language message extracted: {result}")
    print(f"Mixed language statistics: {stats}")
    
    # Test with different email clients
    gmail_msg = """Reply

<div class="gmail_quote">
On Mon, Jan 1, 2024 at 12:00 PM John Doe wrote:
> Original message
</div>"""
    
    result = extract_from(gmail_msg, "text/html")
    print(f"Gmail HTML message extracted: {result[:100]}...")
    
    outlook_msg = """Reply

<div style="border:none;border-top:solid #E1E1E1 1.0pt;padding:3.0pt 0cm 0cm 0cm">
From: John Doe
Date: Mon, 1 Jan 2024 12:00:00 +0000
Subject: Original

Original message
</div>"""
    
    result = extract_from(outlook_msg, "text/html")
    print(f"Outlook HTML message extracted: {result[:100]}...")
    
    print()


def test_batch_processing():
    """Test batch message processing"""
    print("=== Testing Batch Processing ===")
    
    messages = [
        "Message 1\n\n> Quote 1",
        "Message 2\n\n> Quote 2",
        "Message 3\n\n> Quote 3"
    ]
    
    results = batch_extract_messages(messages)
    print(f"Processed {len(results)} messages")
    for i, (original, extracted) in enumerate(zip(messages, results)):
        print(f"Message {i+1}: {len(extracted)} chars (was {len(original)} chars)")
    print()


def test_performance_optimization():
    """Test performance optimization features"""
    print("=== Testing Performance Optimization ===")
    
    # Test pattern caching
    pattern1 = get_cached_pattern(r'\d+')
    pattern2 = get_cached_pattern(r'\d+')  # Should use cached version
    print(f"Pattern caching works: {pattern1 is pattern2}")
    
    # Test with a longer message
    long_msg = "Hello world!\n" * 1000 + "\n> Original message\n" * 500
    result = extract_from(long_msg)
    print(f"Long message processed: {len(result)} chars extracted from {len(long_msg)} chars")
    print()


def test_error_handling():
    """Test error handling"""
    print("=== Testing Error Handling ===")
    
    # Test with empty message
    result = extract_from("")
    print(f"Empty message result: '{result}'")
    
    # Test with None
    try:
        result = extract_from(None)
        print(f"None message result: '{result}'")
    except Exception as e:
        print(f"None message error: {e}")
    
    # Test with invalid HTML
    invalid_html = "<html><body><p>Hello</p><unclosed>"
    result = extract_from(invalid_html, "text/html")
    print(f"Invalid HTML result: {result[:50]}...")
    print()


def main():
    """Run all tests"""
    print("Testing Improved Talon Quotations Module")
    print("=" * 50)
    
    test_basic_extraction()
    test_quotation_extraction()
    test_conversation_thread()
    test_metadata_extraction()
    test_multilingual_support()
    test_batch_processing()
    test_performance_optimization()
    test_error_handling()
    
    print("All tests completed!")


if __name__ == "__main__":
    main() 