# This file is part of nvitop, the interactive NVIDIA-GPU process viewer.
# This file is originally part of ranger, the console file manager. https://github.com/ranger/ranger
# License: GNU GPL version 3.

# pylint: disable=missing-module-docstring,missing-class-docstring,missing-function-docstring

import copy
import curses
import curses.ascii
from collections import OrderedDict


DIGITS = set(map(ord, '0123456789'))

# Arbitrary numbers which are not used with curses.KEY_XYZ
ANYKEY, PASSIVE_ACTION, ALT_KEY, QUANT_KEY = range(9001, 9005)

SPECIAL_KEYS = OrderedDict(
    [
        ('BS', curses.KEY_BACKSPACE),
        ('Backspace', curses.KEY_BACKSPACE),  # overrides <BS> in REVERSED_SPECIAL_KEYS
        ('Backspace2', curses.ascii.DEL),
        ('Delete', curses.KEY_DC),
        ('S-Delete', curses.KEY_SDC),
        ('Insert', curses.KEY_IC),
        ('CR', ord('\n')),
        ('Return', ord('\n')),
        ('Enter', ord('\n')),  # overrides <CR> and <Return> in REVERSED_SPECIAL_KEYS
        ('Space', ord(' ')),
        ('Escape', curses.ascii.ESC),
        ('Esc', curses.ascii.ESC),  # overrides <Escape> in REVERSED_SPECIAL_KEYS
        ('Down', curses.KEY_DOWN),
        ('Up', curses.KEY_UP),
        ('Left', curses.KEY_LEFT),
        ('Right', curses.KEY_RIGHT),
        ('PageDown', curses.KEY_NPAGE),
        ('PageUp', curses.KEY_PPAGE),
        ('Home', curses.KEY_HOME),
        ('End', curses.KEY_END),
        ('Tab', ord('\t')),
        ('S-Tab', curses.KEY_BTAB),
        ('lt', ord('<')),
        ('gt', ord('>')),
    ],
)

NAMED_SPECIAL_KEYS = tuple(SPECIAL_KEYS.keys())
SPECIAL_KEYS_UNCASED = {}
VERY_SPECIAL_KEYS = {
    'Alt': ALT_KEY,
    'any': ANYKEY,
    'bg': PASSIVE_ACTION,
    'allow_quantifiers': QUANT_KEY,
}


def _uncase_special_key(string):
    """Uncase a special key.

    >>> _uncase_special_key('Esc')
    'esc'

    >>> _uncase_special_key('C-X')
    'c-x'
    >>> _uncase_special_key('C-x')
    'c-x'

    >>> _uncase_special_key('A-X')
    'a-X'
    >>> _uncase_special_key('A-x')
    'a-x'
    """
    uncased = string.lower()
    if len(uncased) == 3 and (uncased.startswith(('a-', 'm-'))):
        uncased = f'{uncased[0]}-{string[-1]}'
    return uncased


def _special_keys_init():
    for key, val in tuple(SPECIAL_KEYS.items()):
        SPECIAL_KEYS['M-' + key] = (ALT_KEY, val)
        SPECIAL_KEYS['A-' + key] = (ALT_KEY, val)  # overrides <M-*> in REVERSED_SPECIAL_KEYS

    for char in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_!{}[],./':
        SPECIAL_KEYS['M-' + char] = (ALT_KEY, ord(char))
        SPECIAL_KEYS['A-' + char] = (ALT_KEY, ord(char))  # overrides <M-*> in REVERSED_SPECIAL_KEYS

    # We will need to reorder the keys of SPECIAL_KEYS below.
    # For example, <C-j> will override <Enter> in REVERSE_SPECIAL_KEYS,
    # this makes construct_keybinding(parse_keybinding('<CR>')) == '<C-j>'
    for char in 'abcdefghijklmnopqrstuvwxyz_':
        SPECIAL_KEYS['C-' + char] = ord(char) - 96

    SPECIAL_KEYS['C-Space'] = 0

    for n in range(64):
        SPECIAL_KEYS['F' + str(n)] = curses.KEY_F0 + n

    SPECIAL_KEYS.update(VERY_SPECIAL_KEYS)  # noqa: F821

    # Reorder the keys of SPECIAL_KEYS.
    for key in NAMED_SPECIAL_KEYS:  # noqa: F821
        SPECIAL_KEYS.move_to_end(key, last=True)

    for key, val in SPECIAL_KEYS.items():
        SPECIAL_KEYS_UNCASED[_uncase_special_key(key)] = val


_special_keys_init()
del _special_keys_init, VERY_SPECIAL_KEYS, NAMED_SPECIAL_KEYS
REVERSED_SPECIAL_KEYS = OrderedDict([(v, k) for k, v in SPECIAL_KEYS.items()])


def parse_keybinding(obj):  # pylint: disable=too-many-branches
    r"""Translate a keybinding to a sequence of integers
    The letter case of special keys in the keybinding string will be ignored.

    >>> out = tuple(parse_keybinding('lol<CR>'))
    >>> out
    (108, 111, 108, 10)
    >>> out == (ord('l'), ord('o'), ord('l'), ord('\n'))
    True

    >>> out = tuple(parse_keybinding('x<A-Left>'))
    >>> out
    (120, 9003, 260)
    >>> out == (ord('x'), ALT_KEY, curses.KEY_LEFT)
    True
    """
    assert isinstance(obj, (tuple, int, str))
    if isinstance(obj, tuple):
        yield from obj
    elif isinstance(obj, int):  # pylint: disable=too-many-nested-blocks
        yield obj
    else:  # pylint: disable=too-many-nested-blocks
        in_brackets = False
        bracket_content = []
        for char in obj:
            if in_brackets:
                if char == '>':
                    in_brackets = False
                    string = ''.join(bracket_content)
                    try:
                        keys = SPECIAL_KEYS_UNCASED[_uncase_special_key(string)]
                        yield from keys
                    except KeyError:
                        if string.isdigit():
                            yield int(string)
                        else:
                            yield ord('<')
                            for bracket_char in bracket_content:
                                yield ord(bracket_char)
                            yield ord('>')
                    except TypeError:
                        yield keys  # it was no tuple, just an int
                else:
                    bracket_content.append(char)
            elif char == '<':
                in_brackets = True
                bracket_content = []
            else:
                yield ord(char)
        if in_brackets:
            yield ord('<')
            for char in bracket_content:
                yield ord(char)


def key_to_string(key):
    if key in range(33, 127):
        return chr(key)
    if key in REVERSED_SPECIAL_KEYS:
        return f'<{REVERSED_SPECIAL_KEYS[key]}>'
    return f'<{key}>'


def construct_keybinding(keys):
    """Do the reverse of parse_keybinding.

    >>> construct_keybinding(parse_keybinding('lol<CR>'))
    'lol<Enter>'

    >>> construct_keybinding(parse_keybinding('x<A-Left>'))
    'x<A-Left>'

    >>> construct_keybinding(parse_keybinding('x<Alt><Left>'))
    'x<A-Left>'
    """
    try:
        keys = tuple(keys)
    except TypeError:
        assert isinstance(keys, int)
        keys = (keys,)
    strings = []
    alt_key_on = False
    for key in keys:
        if key == ALT_KEY:
            alt_key_on = True
            continue
        if alt_key_on:
            try:
                strings.append(f'<{REVERSED_SPECIAL_KEYS[(ALT_KEY, key)]}>')
            except KeyError:
                strings.extend(map(key_to_string, (ALT_KEY, key)))
        else:
            strings.append(key_to_string(key))
        alt_key_on = False

    return ''.join(strings)


def normalize_keybinding(keybinding):
    """Normalize a keybinding to a string.

    >>> normalize_keybinding('lol<CR>')
    'lol<Enter>'

    >>> normalize_keybinding('x<A-Left>')
    'x<A-Left>'

    >>> normalize_keybinding('x<Alt><Left>')
    'x<A-Left>'
    """
    return construct_keybinding(parse_keybinding(keybinding))


class KeyMaps(dict):
    def __init__(self, keybuffer=None):
        super().__init__()
        self.keybuffer = keybuffer
        self.used_keymap = None

    def use_keymap(self, keymap_name):
        self.keybuffer.keymap = self.get(keymap_name, {})
        if self.used_keymap != keymap_name:
            self.used_keymap = keymap_name
            self.keybuffer.clear()

    def clear_keymap(self, keymap_name):
        self[keymap_name] = {}
        if self.used_keymap == keymap_name:
            self.keybuffer.keymap = {}
            self.keybuffer.clear()

    def _clean_input(self, context, keys):
        try:
            pointer = self[context]
        except KeyError:
            self[context] = pointer = {}
        keys = keys.encode('utf-8').decode('latin-1')
        return list(parse_keybinding(keys)), pointer

    def bind(self, context, keys, leaf):
        keys, pointer = self._clean_input(context, keys)
        if not keys:
            return
        last_key = keys[-1]
        for key in keys[:-1]:
            if key in pointer and isinstance(pointer[key], dict):
                pointer = pointer[key]
            else:
                pointer = pointer[key] = {}
        pointer[last_key] = leaf

    def copy(self, context, source, target):
        clean_source, pointer = self._clean_input(context, source)
        if not source:
            return
        for key in clean_source:
            try:
                pointer = pointer[key]
            except KeyError as ex:  # noqa: PERF203
                raise KeyError(
                    f'Tried to copy the keybinding `{source}`, but it was not found.',
                ) from ex
        try:
            self.bind(context, target, copy.deepcopy(pointer))
        except TypeError:
            self.bind(context, target, pointer)

    def unbind(self, context, keys):
        keys, pointer = self._clean_input(context, keys)
        if not keys:
            return
        self._unbind_traverse(pointer, keys)

    @staticmethod
    def _unbind_traverse(pointer, keys, pos=0):
        if keys[pos] not in pointer:
            return
        if len(keys) > pos + 1 and isinstance(pointer, dict):
            KeyMaps._unbind_traverse(pointer[keys[pos]], keys, pos=pos + 1)
            if not pointer[keys[pos]]:
                del pointer[keys[pos]]
        elif len(keys) == pos + 1:
            try:
                del pointer[keys[pos]]
            except KeyError:
                pass
            try:
                keys.pop()
            except IndexError:
                pass


class KeyBuffer:  # pylint: disable=too-many-instance-attributes
    any_key = ANYKEY
    passive_key = PASSIVE_ACTION
    quantifier_key = QUANT_KEY
    excluded_from_anykey = [curses.ascii.ESC]

    def __init__(self, keymap=None):
        self.keymap = keymap
        self.keys = []
        self.wildcards = []
        self.pointer = self.keymap
        self.result = None
        self.quantifier = None
        self.finished_parsing_quantifier = False
        self.finished_parsing = False
        self.parse_error = False

        if (
            self.keymap
            and self.quantifier_key in self.keymap
            and self.keymap[self.quantifier_key] == 'false'
        ):
            self.finished_parsing_quantifier = True

    def clear(self):
        self.__init__(self.keymap)  # pylint: disable=unnecessary-dunder-call

    def add(self, key):
        self.keys.append(key)
        self.result = None
        if not self.finished_parsing_quantifier and key in DIGITS:
            if self.quantifier is None:
                self.quantifier = 0
            self.quantifier = self.quantifier * 10 + key - 48  # (48 = ord('0'))
        else:
            self.finished_parsing_quantifier = True

            moved = True
            if key in self.pointer:
                self.pointer = self.pointer[key]
            elif self.any_key in self.pointer and key not in self.excluded_from_anykey:
                self.wildcards.append(key)
                self.pointer = self.pointer[self.any_key]
            else:
                moved = False

            if moved:
                if isinstance(self.pointer, dict):
                    if self.passive_key in self.pointer:
                        self.result = self.pointer[self.passive_key]
                else:
                    self.result = self.pointer
                    self.finished_parsing = True
            else:
                self.finished_parsing = True
                self.parse_error = True

    def __str__(self):
        return construct_keybinding(self.keys)
