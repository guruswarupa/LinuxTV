import QtQuick 2.15

Item {
    id: root
    property string appLabel: ""
    property color splashColor: theme.bg
    opacity: 0
    visible: opacity > 0
    Behavior on opacity { NumberAnimation { duration: 200 } }

    Rectangle {
        anchors.fill: parent
        color: root.splashColor
    }

    Text {
        anchors.centerIn: parent
        text: root.appLabel
        color: "white"
        font.family: theme.font_family
        font.pixelSize: 32
        font.bold: true
    }

    Connections {
        target: backend
        function onLaunchStarted(name, color) {
            root.appLabel = "Opening " + name
            root.splashColor = color
            root.opacity = 1
        }
        function onLaunchFinished() {
            root.opacity = 0
        }
    }
}
