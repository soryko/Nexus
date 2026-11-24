"""Frozen before replay: require two distinct content terms in the hit excerpt.

Do not consult the key, labels, type, rank score, other bodies, or prior cases.
Only consider baseline-eligible hits; never backfill from lower ranks.
"""
import re

STOP = set('a an and are as at be by did do does for from how in is it of on or that the this to was were what when which who why with'.split())


def terms(text):
    return set(re.findall('[a-z0-9]+', text.lower())) - STOP


def admit(query, excerpt):
    return len(terms(query) & terms(excerpt)) >= 2
