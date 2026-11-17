# lanchat

Codes by Deepseek

## 主要功能

1. 局域网聊天
    - 在同一局域网内畅聊，有私聊功能！

2. 结束极域
    - 使用 ``taskkill /F /IM xxx`` 结束极域进程

3. CS1.6支持
    - 软件编译成果内置CS1.6，首次启动自动解压 [^1]

更多功能敬请期待！

## 使用方式

### 使用编译成果

- 直接进入release自取.exe运行即可
    1. 初次运行需要解压CS，时间可能稍久
    2. 开始运行时需要设置昵称

### 自行编译

#### 编译前

1. 确保已部署python环境
    - 若没有，[下载Python](https://www.python.org/downloads/)
2. 确保目标python中含有pyinstaller模块
    - 若没有，运行 ``pip install pyinstaller`` 下载所需模块

#### 编译步骤

1. 使用 ``git clone`` 克隆此仓库
2. 在仓库目录下使用 ``pyinstaller lanchat.spec`` 可自动编译，成果在 ``.\dist``目录下

## 贡献者名单

<a href="https://github.com/yjl214/lanchat/graphs/contributors">
    <img src="https://contrib.rocks/image?repo=yjl214/lanchat" />
</a>

Made with [contrib.rocks](https://contrib.rocks).

[^1]:可以将自己的CS放至软件同目录下，将使用此版本CS
