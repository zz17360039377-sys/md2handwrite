# C++ 面向对象基础

## 一、编译原理

编译型先整体翻译成机器码再执行，如 C、C++；解释型边解释边执行，如 Python
编译型优势：快、可优化、可静态查错；机器人实时性要求高，ROS 核心节点用 C++

```
g++ hello.cpp -o hello -Wall -std=c++17
g++ -E → .i    g++ -S → .s    g++ -c → .o    g++ *.o -o hello
```

预处理展开头文件宏定义；编译生成汇编，语法错误在此报；汇编生成机器码 .o；链接拼成可执行文件，**undefined reference 出在链接**
g++ 按 C++ 处理并自动链接 C++ 标准库；gcc 默认按 C 处理

## 二、C++ 介绍

面向过程缺点：函数与数据无直观联系；无封装隐藏，扩展性差，难查错重用
新增特性：类和对象、继承、多态虚函数、引用、名称空间、函数重载、泛型编程
**三大特性：封装、继承、多态**

## 三、类和对象

类是抽象模板，对象是具体个体，创建对象叫实例化；类四要素：类名、访问修饰符、属性、方法

```
class Circle {
public:
    double R;
    double cal_zc();
};
Circle c;        // 实例化
c.R = 1.5;       // 点号访问公有成员
```

public 任何地方可访问；private 仅类内和友元；protected 类内和子类内
**class 默认 private，struct 默认 public，唯一区别**
类外实现成员函数要加 类名双冒号

## 四、构造与析构

构造函数与类同名，自动调用，为成员赋值；析构类名前加波浪号，无参无返回值，消亡前自动调用

```
class Person {
public:
    Person() { }                            // 无参
    Person(string n, int a) { }             // 有参
    Person(const Person &p) { }             // 拷贝构造：const 引用省空间
    ~Person() { }                           // 析构：清理 new 的内存
};
Person p1;
Person p2("tom", 18);      // 括号法
Person p3 = Person("j", 20);  // 显式法
Person p4 = p2;            // 拷贝构造
```

this 指向当前对象；不提供构造时编译器自动给默认无参构造
有参构造三种调用：括号法、显式法、隐式法

## 五、静态成员

静态成员变量：所有对象共享一份数据；编译阶段分配内存；类内声明类外初始化
静态成员函数：共享同一个函数；只能访问静态成员变量

```
static int count;          // 类内声明
int Person::count = 0;     // 类外初始化
Person::count 或 p1.count  // 两种访问
```

## 六、封装与友元

封装：属性私有，对外提供接口；接口内可过滤非法值，如 setAge
友元不是成员函数，但能访问私有和保护成员；友元也可以是类

## 七、继承

继承实现代码复用；已有类叫基类，新类叫派生类
语法：class 子类 冒号 继承方式 父类

```
class Animal {
public:
    void eat();
};
class Dog : public Animal {   // 公有继承
public:
    void bark();
};
```

三种继承方式：public、protected、private
公有继承：子类内可访问父类 public 和 protected，不能访问 private；类外只能访问 public
保护继承：public 和 protected 都变保护；私有继承：都变私有，孙类也访问不到

**继承中的构造析构：先父构造后子构造；父构造有参数要在子类初始化列表显式调用；析构顺序与构造相反**

## 八、多态

父类的指针或引用可以指向子类对象
父类函数加 virtual 即虚函数；调用地址运行时才确定，称动态联编
**多态两条件：一有继承关系；二子类重写父类虚函数**
好处：只对抽象基类编程，提高复用；向后兼容

```
class Animal {
public:
    virtual void speak() { }
};
class Cat : public Animal {
public:
    void speak() { }
};
void DoSpeak(Animal &a) { a.speak(); }
Cat cat;  DoSpeak(cat);
```

## 九、模板

模板是泛型编程基础，把数据类型作为参数传递，提高复用

```
template<typename T>
T myAdd(T a, T b) { return a + b; }
myAdd(1, 2);          // 自动推导
myAdd<int>(1, 2);     // 显式指定
```

自动推导必须推出一致的 T；模板必须确定 T 才能用

```
template<class T>
class Box { T val; };
Box<int> b;            // 类模板不能自动推导，必须显式指定
```

类模板参数可有默认值；类外实现要加模板参数列表

## 十、范围 for，C++11

冒号分两部分：迭代变量 和 迭代范围；auto 自动推导
e 为引用：可修改元素且省空间；非引用：拷贝，效率低改不了

```
for (auto e : arr)  { }
for (auto &e : arr) { e++; }
```
