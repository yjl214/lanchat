# lanchat

Codes by Deepseek

## 主要功能

1. 局域网聊天
    - 在同一局域网内畅聊，有私聊功能！

2. 结束极域
    - 使用 ``taskkill /F /IM xxx`` 结束极域进程

3. CS1.6支持
    - 首次启动自动从 7z 直链下载并解压 CS1.6，下载过程中可取消 [^1]

更多功能敬请期待！

## 使用方式

### 使用编译成果

- 直接进入release自取.exe运行即可
    1. 初次运行需要下载并解压CS，时间取决于网速
    2. 开始运行时需要设置昵称

> [!IMPORTANT]
> CS1.6 压缩包的 7z 直链在 `lanchat.py` 顶部的 `CS_ARCHIVE_URL` 中配置（默认会有一个公共地址），也可以用环境变量 `LANCHAT_CS_URL` 覆盖。取消下载或出错时会自动删除下载残留。

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

[![Contributors](https://contrib.rocks/image?repo=yjl214/lanchat)](https://github.com/yjl214/lanchat/graphs/contributors)
