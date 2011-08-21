import datetime
import decimal
import math
from time import localtime

from psycopg2 import libpq


encodings = {
    'UNICODE': 'utf_8',
    'UTF8': 'utf_8',
    'LATIN1': 'ISO-8859-1',
    'LATIN2': 'ISO-8859-2',
    'LATIN3': 'ISO-8859-3',
    'LATIN4': 'ISO-8859-4',
    'LATIN5': 'ISO-8859-9',
    'LATIN6': 'ISO-8859-10',
    'LATIN7': 'ISO-8859-13',
    'LATIN8': 'ISO-8859-14',
    'LATIN9': 'ISO-8859-15',
    'LATIN10': 'ISO-8859-16'
}

string_types = {}


class Type(object):
    def __init__(self, name, values, caster=None, py_caster=None, base_caster=None):
        self.name = name
        self.values = values
        self.caster = caster
        self.base_caster = base_caster
        self.py_caster = py_caster

    def __eq__(self, other):
        return other in self.values

    def cast(self, value, length, cursor):
        if self.py_caster is not None:
            return self.py_caster(value, cursor)
        return self.caster(value, length, cursor)


def register_type(type_obj, scope=None):

    typecasts = string_types
    if scope:
        from psycopg2.connection import Connection
        from psycopg2.cursor import Cursor

        if isinstance(scope, Connection):
            typecasts = scope._typecasts
        elif isinstance(scope, Cursor):
            typecasts = scope._typecasts
        else:
            typecasts = None

    _register_type(type_obj, typecasts)


def _register_type(obj, typecasts):
    for value in obj.values:
        typecasts[value] = obj


def new_type(oids, name, adapter):
    return Type(name, oids, py_caster=adapter)


def typecast(caster, value, length, cursor):
    old = cursor._caster
    cursor._caster = caster
    val = caster.cast(value, length, cursor)
    cursor._caster = old
    return val


def cast_string(value, length, cursor):
    return value


def cast_longinteger(value, length, cursor):
    return long(value)


def cast_integer(value, length, cursor):
    return int(value)


def cast_float(value, length, cursor):
    return float(value)


def cast_decimal(value, length, cursor):
    return decimal.Decimal(value)


def cast_binary(value, length, cursor):
    to_length = libpq.c_uint()
    s = libpq.PQunescapeBytea(value, libpq.pointer(to_length))
    try:
        res = buffer(s[:to_length.value])
    finally:
        libpq.PQfreemem(s)
    return res


def cast_boolean(value, length, cursor):
    return value[0] == "t"


def cast_generic_array(value, length, cursor):
    s = value
    assert s[0] == "{" and s[-1] == "}"
    i = 1
    array = []
    stack = [array]
    while i < len(s) - 1:
        if s[i] == "{":
            sub_array = []
            array.append(sub_array)
            stack.append(sub_array)
            array = sub_array
            i += 1
        elif s[i] == "}":
            stack.pop()
            array = stack[-1]
            i += 1
        elif s[i] == ",":
            i += 1
        else:
            start = i
            # If q is odd this is quoted
            q = 0
            # Whether or not the last char was a backslash
            b = False
            while i < len(s) - 1:
                if s[i] == '"':
                    if not b:
                        q += 1
                elif s[i] == "\\":
                    b = not b
                elif s[i] == "}" or s[i] == ",":
                    if not b and q % 2 == 0:
                        break
                i += 1
            if q:
                start += 1
                end = i - 1
            else:
                end = i

            val = []
            for j in xrange(start, end):
                if s[j] != "\\" or s[j - 1] == "\\":
                    val.append(s[j])
            str_buf = "".join(val)
            val = typecast(
                cursor._caster.base_caster, str_buf, end - start, cursor
            )
            array.append(val)
    return stack[-1]


def cast_unicode(value, length, cursor):
    encoding = encodings[cursor.connection.encoding]
    return value.decode(encoding)


def _parse_date(value):
    return datetime.date(*map(int, value.split('-')))


def _parse_time(time, cursor):
    microsecond = 0
    hour, minute, second = time.split(":", 2)

    tzinfo = None
    sign = 0
    timezone = None
    if "-" in second:
        sign = -1
        second, timezone = second.split("-")
    elif "+" in second:
        sign = 1
        second, timezone = second.split("+")
    if not cursor.tzinfo_factory is None and sign:
        parts = timezone.split(":")
        tz_min = sign * 60 * int(parts[0])
        if len(parts) > 1:
            tz_min += int(parts[1])
        if len(parts) > 2:
            tz_min += int(int(parts[2]) / 60.)
        tzinfo = cursor.tzinfo_factory(tz_min)
    if "." in second:
        second, microsecond = second.split(".")
        microsecond = int(microsecond) * int(math.pow(10.0, 6.0 - len(microsecond)))

    return datetime.time(int(hour), int(minute), int(second), microsecond,
        tzinfo)


def cast_datetime(value, length, cursor):
    date, time = value.split(' ')
    date = _parse_date(date)
    time = _parse_time(time, cursor)
    return datetime.datetime.combine(date, time)


def cast_date(value, length, cursor):
    return _parse_date(value)


def cast_time(value, length, cursor):
    return _parse_time(value, cursor)


def cast_interval(value, length, cursor):
    years = months = days = 0
    hours = minutes = seconds = hundreths = 0.0
    v = 0.0
    sign = 1
    denominator = 1.0
    part = 0
    skip_to_space = False

    s = value
    for c in s:
        if skip_to_space:
            if c == " ":
                skip_to_space = False
            continue
        if c == "-":
            sign = -1
        elif "0" <= c <= "9":
            v = v * 10 + ord(c) - ord("0")
            if part == 6:
                denominator *= 10
        elif c == "y":
            if part == 0:
                years = int(v * sign)
                skip_to_space = True
                v = 0.0
                sign = 1
                part = 1
        elif c == "m":
            if part <= 1:
                months = int(v * sign)
                skip_to_space = True
                v = 0.0
                sign = 1
                part = 2
        elif c == "d":
            if part <= 2:
                days = int(v * sign)
                skip_to_space = True
                v = 0.0
                sign = 1
                part = 3
        elif c == ":":
            if part <= 3:
                hours = v
                v = 0.0
                part = 4
            elif part == 4:
                minutes = v
                v = 0.0
                part = 5
        elif c == ".":
            if part == 5:
                seconds = v
                v = 0.0
                part = 6

    if part == 4:
        minutes = v
    elif part == 5:
        seconds = v
    elif part == 6:
        hundreths = v / denominator

    if sign < 0.0:
        seconds = - (hundreths + seconds + minutes * 60 + hours * 3600)
    else:
        seconds += hundreths + minutes * 60 + hours * 3600

    days += years * 365 + months * 30
    micro = (seconds - math.floor(seconds)) * 1000000.0
    seconds = int(math.floor(seconds))
    return datetime.timedelta(days, seconds, int(micro))


def Date(year, month, day):
    from psycopg2.extensions.adapters import DateTime
    date = datetime.date(year, month, day)
    return DateTime(date)


def DateFromTicks(ticks):
    tm = localtime()
    return Date(tm.tm_year, tm.tm_mon, tm.tm_mday)


def Binary(obj):
    from psycopg2.extensions.adapters import Binary
    return Binary(obj)
