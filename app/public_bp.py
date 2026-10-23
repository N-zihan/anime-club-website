"""public 蓝图对象单独放一个文件，便于拆分子模块时共享（endpoint 名不变）。"""
from flask import Blueprint

public_bp = Blueprint('public', __name__)
